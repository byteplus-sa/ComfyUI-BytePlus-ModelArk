import base64
import io
import json
import os
import asyncio
import hashlib
import torch
import aiohttp
from comfy_api.latest import io as comfy_io
from PIL import Image

try:
    from comfy_api.input_impl import VideoFromFile
except ImportError:
    VideoFromFile = None

from .nodes_shared import (
    GLOBAL_CATEGORY,
    BytePlusClientType,
    BytePlusException,
    _tensor2images,
    log_msg,
    format_api_error,
    upload_file_to_ark,
    get_node_count_in_workflow,
    api_key_fingerprint,
)
from .executor import BytePlusVisualExecutor
from .constants import DEFAULT_VISUAL_SYSTEM_PROMPT, DEFAULT_VISUAL_USER_PROMPT
from .models_config import VISUAL_MODEL_MAP, VISUAL_UI_OPTIONS

# Last response per Visual node, for multi-turn (previous_response_id).
# Keyed by node id so parallel nodes keep separate conversations. A response ID
# only exists in the account and region that made it, so those are stored too
# (the key as a fingerprint). Oldest entries are dropped past the limit.
LAST_RESPONSES = {}
LAST_RESPONSES_MAX = 256


def _conversation_owner(client):
    api_key = getattr(client, "api_key", None) or ""
    return (api_key_fingerprint(api_key) if api_key else "", getattr(client, "region", None))

class BytePlusVisualUnderstanding(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusVisualUnderstanding",
            display_name="BytePlus Visual Understanding (Legacy)",
            category=GLOBAL_CATEGORY,
            description=(
                "Legacy node, kept so saved workflows still load: use BytePlus Seed instead. "
                "Understand images and video with Dola Seed 2.1 Turbo by default, "
                "or Seed 2.0 Pro / Lite / Mini."
            ),
            is_deprecated=True,
            inputs=[
                BytePlusClientType.Input("client"),
                comfy_io.Combo.Input("model", options=VISUAL_UI_OPTIONS, default=VISUAL_UI_OPTIONS[0]),
                comfy_io.String.Input("system_prompt", multiline=True, default=DEFAULT_VISUAL_SYSTEM_PROMPT),
                comfy_io.String.Input("user_prompt", multiline=True, default=DEFAULT_VISUAL_USER_PROMPT),
                comfy_io.Combo.Input("detail", options=["low", "high"], default="high"),
                comfy_io.Float.Input("fps", default=1.0, min=0.2, max=5.0, step=0.1),
                comfy_io.Combo.Input("reasoning_mode", options=["auto", "enabled", "disabled"], default="auto"),
                comfy_io.Combo.Input("reasoning_effort", options=["minimal", "low", "medium", "high"], default="medium"),
                comfy_io.Int.Input("turns", default=1, min=1, max=10),
                comfy_io.Boolean.Input("stream", default=False, display_name="Enable Streaming"),
                comfy_io.Int.Input("file_expire_seconds", default=604800, min=86400, max=2592000, step=1),
                comfy_io.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff), 
                
                comfy_io.MultiType.Input(
                    comfy_io.Image.Input("visual_input_1", optional=True),
                    types=[comfy_io.Image, comfy_io.Video]
                ),
                comfy_io.MultiType.Input(
                    comfy_io.Image.Input("visual_input_2", optional=True),
                    types=[comfy_io.Image, comfy_io.Video]
                ),
                comfy_io.MultiType.Input(
                    comfy_io.Image.Input("visual_input_3", optional=True),
                    types=[comfy_io.Image, comfy_io.Video]
                ),
            ],
            outputs=[
                comfy_io.String.Output(display_name="content"),
                comfy_io.String.Output(display_name="raw_json"),
            ],
            hidden=[comfy_io.Hidden.prompt, comfy_io.Hidden.unique_id],
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        system_prompt,
        user_prompt,
        seed, 
        stream,
        file_expire_seconds,
        detail,
        fps,
        reasoning_mode="auto",
        reasoning_effort="medium",
        turns=1,
        visual_input_1=None,
        visual_input_2=None,
        visual_input_3=None,
    ) -> comfy_io.NodeOutput:
        
        effective_system_prompt = (system_prompt or "").strip() or DEFAULT_VISUAL_SYSTEM_PROMPT
        inputs_content = []
        normalized_file_expire_seconds = int(file_expire_seconds if file_expire_seconds is not None else 604800)
        normalized_file_expire_seconds = max(86400, min(normalized_file_expire_seconds, 2592000))
        
        if user_prompt:
            inputs_content.append({"type": "input_text", "text": user_prompt})

        visual_inputs = [visual_input_1, visual_input_2, visual_input_3]
        
        for i, v_input in enumerate(visual_inputs):
            if v_input is None:
                continue
            
            log_msg("visual_processing_input", i=i+1, type=type(v_input))

            file_id = None
            input_type = "input_image"

            file_path = None
            
            if isinstance(v_input, torch.Tensor):
                pil_imgs = _tensor2images(v_input)
                if len(pil_imgs) > 0:
                    import folder_paths
                    img = pil_imgs[0]
                    image_buffer = io.BytesIO()
                    img.save(image_buffer, format="JPEG", quality=95)
                    image_bytes = image_buffer.getvalue()
                    image_hash = hashlib.sha256(image_bytes).hexdigest()
                    input_dir = folder_paths.get_input_directory()
                    cache_dir = os.path.join(input_dir, "BytePlusVisualCache")
                    os.makedirs(cache_dir, exist_ok=True)
                    file_path = os.path.join(cache_dir, f"byteplus_visual_cache_{image_hash}.jpg")
                    if not os.path.exists(file_path):
                        with open(file_path, "wb") as f:
                            f.write(image_bytes)
                    input_type = "input_image"
                    
            else:
                if isinstance(v_input, str):
                    file_path = v_input
                elif VideoFromFile and isinstance(v_input, VideoFromFile):
                    file_path = getattr(v_input, "path", None)
                    if not file_path and hasattr(v_input, "_VideoFromFile__file"):
                        potential_file = getattr(v_input, "_VideoFromFile__file")
                        if isinstance(potential_file, str):
                             file_path = potential_file

                elif hasattr(v_input, "path"):
                    file_path = v_input.path
                elif isinstance(v_input, dict):
                    if "video_path" in v_input:
                        file_path = v_input["video_path"]
                    elif "filenames" in v_input:
                        files = v_input["filenames"]
                        if isinstance(files, list) and len(files) > 0:
                            file_path = files[0]
                    elif "path" in v_input:
                        file_path = v_input["path"]

                if file_path and not os.path.exists(file_path):
                     import folder_paths
                     input_dir = folder_paths.get_input_directory()
                     potential_path = os.path.join(input_dir, file_path)
                     if os.path.exists(potential_path):
                         log_msg("visual_found_file", path=potential_path)
                         file_path = potential_path
            
            if file_path and os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                if ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".gif"]:
                    input_type = "input_video"
            
            if file_path and os.path.exists(file_path):
                file_id = await upload_file_to_ark(
                    client,
                    file_path,
                    fps=fps if input_type == "input_video" else None,
                    expire_seconds=normalized_file_expire_seconds,
                    model=VISUAL_MODEL_MAP.get(model, model),
                )

            if file_id:
                content_item = {
                    "type": input_type,
                    "file_id": file_id
                }
                if input_type == "input_image":
                    content_item["detail"] = detail
                
                inputs_content.append(content_item)

        node_id = cls.hidden.unique_id
        owner = _conversation_owner(client)

        full_content = ""
        final_json_str = "{}"
        
        previous_response_id = None
        
        last = LAST_RESPONSES.get(node_id)
        if turns > 1 and last and last.get("owner") == owner:
            previous_response_id = last["id"]
            log_msg("visual_cont_conv", id=previous_response_id)
        else:
            log_msg("visual_new_conv")
        
        model_id = VISUAL_MODEL_MAP.get(model, model)
        
        payload = {
            "model": model_id,
        }
        
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
            
            follow_up_content = []
            if user_prompt:
                follow_up_content.append({"type": "input_text", "text": user_prompt})
            else:
                follow_up_content.append({"type": "input_text", "text": "Continue."}) 
            
            payload["input"] = [
                {
                    "role": "user",
                    "content": follow_up_content
                }
            ]
            
        else:
            payload["input"] = [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": effective_system_prompt}]
                },
                {
                    "role": "user",
                    "content": inputs_content
                }
            ]
        
        if reasoning_mode != "auto":
            if "thinking" not in payload:
                payload["thinking"] = {}
            payload["thinking"]["type"] = reasoning_mode
            
        if reasoning_effort != "medium":
            if "reasoning" not in payload:
                payload["reasoning"] = {}
            payload["reasoning"]["effort"] = reasoning_effort
            
        current_response_json = {}
        
        executor = BytePlusVisualExecutor(client)

        if stream:
            node_count = get_node_count_in_workflow("BytePlusVisualUnderstanding", prompt=cls.hidden.prompt)
            is_single_node = node_count <= 1
            full_content, final_json_str = await executor.stream_response_task(payload, is_single_node=is_single_node)
            try:
                current_response_json = json.loads(final_json_str)
            except:
                pass
        else:
            task_id = await executor.create_response_task(payload)
            current_response_json = await executor.poll_response_result(task_id)
            
            if "output" in current_response_json:
                for item in current_response_json["output"]:
                    if item.get("role") == "assistant":
                        for content_item in item.get("content", []):
                            if content_item.get("type") == "output_text":
                                full_content += content_item.get("text", "")
            
            final_json_str = json.dumps(current_response_json, indent=2, ensure_ascii=False)
        
        if "id" in current_response_json:
            LAST_RESPONSES.pop(node_id, None)
            LAST_RESPONSES[node_id] = {"id": current_response_json["id"], "owner": owner}
            while len(LAST_RESPONSES) > LAST_RESPONSES_MAX:
                LAST_RESPONSES.pop(next(iter(LAST_RESPONSES)))
            log_msg("visual_cached_id", id=current_response_json["id"])
        
        return comfy_io.NodeOutput(full_content, final_json_str)
