import threading
import time
import logging
from .nodes_shared import BytePlusException, get_text, BytePlusClients
from .constants import VIDEO_FRAME_RATE, VIDEO_RESOLUTION_PIXELS

logger = logging.getLogger("BytePlus")

class QuotaManager:
    _instance = None
    _lock = threading.RLock()

    def __init__(self):
        self._quotas = {}
        self._running_counts = {}

    @classmethod
    def instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_quota(self, api_key: str, model: str, limit: int, quota_type: str):
        """
        Set a quota.
        """
        from .nodes_shared import log_msg
        
        with self._lock:
            if api_key not in self._quotas:
                self._quotas[api_key] = {}
            
            if limit <= 0:
                if model in self._quotas[api_key]:
                    del self._quotas[api_key][model]
            else:
                self._quotas[api_key][model] = {
                    "limit": limit,
                    "used": 0,
                    "type": quota_type
                }
            
            log_msg("quota_set_log", model=model, limit=limit, type=quota_type)

    def get_status(self, api_key: str) -> str:
        """
        Return the current quota status as text.
        """
        with self._lock:
            if api_key not in self._quotas or not self._quotas[api_key]:
                return "No active quotas."
            
            lines = ["Updated Quota:"]
            for model, data in self._quotas[api_key].items():
                limit = data["limit"]
                used = data["used"]
                q_type = data["type"]
                unit = "images" if q_type == "image" else "tokens"
                lines.append(f"{model}: {used}/{limit} {unit}")
            
            return "\n".join(lines)

    def check_quota(self, api_key: str, model: str, estimated_cost: int):
        """
        Raise if the estimated cost would exceed the quota.
        """
        with self._lock:
            if api_key not in self._quotas:
                return
            
            if model not in self._quotas[api_key]:
                return

            data = self._quotas[api_key][model]
            limit = data["limit"]
            used = data["used"]

            if used + estimated_cost > limit:
                del self._quotas[api_key][model]
                
                msg = get_text("quota_exceeded").format(
                    model=model,
                    limit=limit,
                    used=used,
                    estimated=estimated_cost
                )
                raise BytePlusException(msg)

    def update_usage(self, api_key: str, model: str, actual_cost: int):
        """
        Record actual usage.
        """
        from .nodes_shared import log_msg

        with self._lock:
            if api_key not in self._quotas:
                return
            
            if model not in self._quotas[api_key]:
                return

            self._quotas[api_key][model]["used"] += actual_cost
            log_msg("quota_update_log", model=model, cost=actual_cost, total=self._quotas[api_key][model]['used'])

    def estimate_video_tokens(self, model: str, width: int, height: int, duration: float, fps: float, has_audio: bool = False, is_draft: bool = False) -> int:
        """
        Estimate video token usage.
        """
        # Base formula: (width * height * fps * duration) / 1024
        base_tokens = (width * height * fps * duration) / 1024.0
        
        if is_draft:
            if "seedance-1.5-pro" in model or "seedance-1-5-pro" in model.replace(".", "-"):
                coeff = 0.6 if has_audio else 0.7
                return int(base_tokens * coeff)
            
            return int(base_tokens)
            
        return int(base_tokens)


from comfy_api.latest import io as comfy_io
from .nodes_shared import GLOBAL_CATEGORY, BytePlusClientType, build_default_client, optional_client_input, with_default_client
from .core_style import raise_if_model_retired
from .models_config import RETIRED_VIDEO_UI_OPTIONS, SEEDREAM_4_MODEL_MAP, VIDEO_MODEL_MAP, SEEDREAM_5_MODEL_MAP

class BytePlusQuotaSettings(comfy_io.ComfyNode):
    """
    Quota settings node: caps image count and video tokens per API key and model.
    Connect generation nodes to its client output so the quota is set before
    they run.
    """
    
    IMAGE_MODELS = ["None"] + list(SEEDREAM_5_MODEL_MAP.keys()) + list(SEEDREAM_4_MODEL_MAP.keys())
    # Retired models stay listed (last) so saved workflows load; running explains the switch.
    VIDEO_MODELS = ["None"] + list(VIDEO_MODEL_MAP.keys()) + RETIRED_VIDEO_UI_OPTIONS

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusQuotaSettings",
            display_name="BytePlus Quota Settings",
            category=GLOBAL_CATEGORY,
            description=(
                "Cap image count and video tokens per model for this API key. Connect "
                "generation nodes to the client output so the quota applies before they run."
            ),
            inputs=[
                optional_client_input(),
                comfy_io.Combo.Input("image_model", options=cls.IMAGE_MODELS, default="None"),
                comfy_io.Int.Input("image_limit", default=0, min=0, max=2147483647, tooltip="0 to disable"),
                comfy_io.Combo.Input("video_model", options=cls.VIDEO_MODELS, default="None"),
                comfy_io.Int.Input("video_limit", default=0, min=0, max=2147483647, tooltip="0 to disable"),
            ],
            outputs=[
                comfy_io.String.Output(display_name="status"),
                BytePlusClientType.Output(
                    display_name="client",
                    tooltip="The same client, passed through after the quota is set.",
                ),
            ],
            # A settings node: it applies the limits even when nothing uses its outputs.
            # It starts no paid work, so it does not need core's "only run when used" rule.
            is_output_node=True,
        )

    @classmethod
    @with_default_client("client", build_default_client)
    def execute(
        cls,
        client,
        image_model,
        image_limit,
        video_model,
        video_limit,
    ) -> comfy_io.NodeOutput:
        
        api_key = getattr(client, "api_key", None)
        
        if not api_key:
            return comfy_io.NodeOutput("Error: Client has no API Key bound.", client)

        manager = QuotaManager.instance()
        
        if image_model != "None":
            real_image_model = SEEDREAM_5_MODEL_MAP.get(image_model, SEEDREAM_4_MODEL_MAP.get(image_model, image_model))
            manager.set_quota(api_key, real_image_model, image_limit, "image")
            
        if video_model != "None":
            raise_if_model_retired(video_model)
            real_video_model = VIDEO_MODEL_MAP.get(video_model, video_model)
            manager.set_quota(api_key, real_video_model, video_limit, "video")

        status = manager.get_status(api_key)
        return comfy_io.NodeOutput(status, client)
