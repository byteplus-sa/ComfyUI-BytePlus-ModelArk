"""
Seedance 1.x nodes shaped like ComfyUI core's ByteDance Text to Video, Image to Video and First-Last-Frame to Video nodes.

Inputs, defaults and tooltips copy core's ByteDanceTextToVideoNode,
ByteDanceImageToVideoNode and ByteDanceFirstLastFrameNode
(comfy_api_nodes/nodes_bytedance.py), with BytePlus's limits where they
differ (duration 2-12 s). Seedance 1.5 Pro, core's third model, is deprecated
by BytePlus (shut down on 2026-11-11), so it and its generate_audio input are left out. Every
node also takes this pack's API Client as its first input and ends with this
pack's extras (advanced). The request goes straight to BytePlus ModelArk:
parameters as JSON body fields, frames as base64 data URIs (the Legacy
Seedance 1.x request code).
"""
from comfy_api.latest import io as comfy_io

from .constants import (
    DEFAULT_FILENAME_PREFIX,
    IMAGE_MAX_EDGE,
    IMAGE_MAX_RATIO,
    IMAGE_MIN_EDGE,
    IMAGE_MIN_RATIO,
    VIDEO_DEFAULT_TIMEOUT,
)
from .core_style import (
    NonBlockingRerun,
    core_search_aliases,
    last_frame_batch_output,
    seed_input,
    video_extra_inputs,
    video_list_output,
    watermark_input,
)
from .models_config import (
    SEEDANCE_1_DEFAULT_DURATION,
    SEEDANCE_1_DEFAULT_MODEL,
    SEEDANCE_1_FLF_DEFAULT_MODEL,
    SEEDANCE_1_FLF_MODEL_OPTIONS,
    SEEDANCE_1_IMAGE_RATIOS,
    SEEDANCE_1_MAX_DURATION,
    SEEDANCE_1_MIN_DURATION,
    SEEDANCE_1_MODEL_OPTIONS,
    SEEDANCE_1_MODELS,
    SEEDANCE_1_RESOLUTIONS,
    SEEDANCE_1_TEXT_RATIOS,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    BytePlusClientType,
    BytePlusException,
    get_text,
)
from .nodes_video import BytePlusVideoBase, build_seedance1_frame_content

# Flags ModelArk would also read from the prompt text; here they are widgets
# (core's list plus the Legacy nodes' "dur" and "frames").
FORBIDDEN_PROMPT_FLAGS = [
    "resolution",
    "ratio",
    "dur",
    "frames",
    "seed",
    "camerafixed",
    "watermark",
]


def resolve_seedance1_model(model, options):
    """Core's option label -> BytePlus model ID, limited to the node's options."""
    if model not in options or model not in SEEDANCE_1_MODELS:
        raise BytePlusException(get_text("err_model_not_supported", model=model))
    return SEEDANCE_1_MODELS[model]


def validate_seedance1_prompt(prompt):
    """Core's validate_string(prompt, strip_whitespace=True, min_length=1)."""
    if not str(prompt or "").strip():
        raise BytePlusException(get_text("err_seedance1_prompt_empty"))


def validate_seedance1_frame(helper, name, image):
    """
    Core's frame checks: 300-6000 px per side, then aspect ratio 0.4-2.5
    (both inclusive).
    """
    if image is None:
        raise BytePlusException(get_text("err_seedance1_image_missing", name=name))
    width, height = helper._extract_image_hw(image)
    if not (
        IMAGE_MIN_EDGE <= width <= IMAGE_MAX_EDGE
        and IMAGE_MIN_EDGE <= height <= IMAGE_MAX_EDGE
    ):
        raise BytePlusException(
            get_text(
                "err_seedance1_image_size",
                name=name,
                min=IMAGE_MIN_EDGE,
                max=IMAGE_MAX_EDGE,
                width=width,
                height=height,
            )
        )
    ratio = float(width) / float(height)
    if not (IMAGE_MIN_RATIO <= ratio <= IMAGE_MAX_RATIO):
        raise BytePlusException(
            get_text(
                "err_seedance1_image_ratio",
                name=name,
                min=IMAGE_MIN_RATIO,
                max=IMAGE_MAX_RATIO,
                ratio=f"{ratio:.4f}",
            )
        )


# --- Inputs (core's definitions) ---

def _model_input(options, default):
    return comfy_io.Combo.Input("model", options=list(options), default=default)


def _prompt_input():
    return comfy_io.String.Input(
        "prompt",
        multiline=True,
        tooltip="The text prompt used to generate the video.",
    )


def _resolution_input():
    return comfy_io.Combo.Input(
        "resolution",
        options=list(SEEDANCE_1_RESOLUTIONS),
        tooltip="The resolution of the output video.",
    )


def _aspect_ratio_input(options):
    return comfy_io.Combo.Input(
        "aspect_ratio",
        options=list(options),
        tooltip="The aspect ratio of the output video.",
    )


def _duration_input():
    return comfy_io.Int.Input(
        "duration",
        default=SEEDANCE_1_DEFAULT_DURATION,
        min=SEEDANCE_1_MIN_DURATION,
        max=SEEDANCE_1_MAX_DURATION,
        step=1,
        tooltip="The duration of the output video in seconds.",
        display_mode=comfy_io.NumberDisplay.slider,
    )


def _core_option_inputs():
    return [
        seed_input(tooltip="Seed to use for generation.", optional=True),
        comfy_io.Boolean.Input(
            "camera_fixed",
            default=False,
            tooltip="Specifies whether to fix the camera. The platform appends an instruction "
            "to fix the camera to your prompt, but does not guarantee the actual effect.",
            optional=True,
            advanced=True,
        ),
        watermark_input(
            tooltip='Whether to add an "AI generated" watermark to the video.',
            optional=True,
        ),
    ]


def _extra_inputs():
    """This pack's extras, after core's inputs."""
    extras = video_extra_inputs(include_offline=True)
    # Core's seed/camera_fixed/watermark are optional, and the frontend lists
    # required inputs before optional ones: keep the extras optional too so
    # they stay last.
    for item in extras:
        item.optional = True
    return extras


def _outputs():
    return [
        video_list_output(),
        last_frame_batch_output(),
        comfy_io.String.Output(
            "response",
            tooltip="Task results as JSON, or the pending task IDs of a non_blocking run.",
        ),
    ]


def _schema(node_id, display_name, description, model_options, default_model, frame_inputs, ratios):
    return comfy_io.Schema(
        node_id=node_id,
        display_name=display_name,
        search_aliases=core_search_aliases(node_id),
        category=GLOBAL_CATEGORY,
        description=description,
        inputs=[
            BytePlusClientType.Input("client"),
            _model_input(model_options, default_model),
            _prompt_input(),
            *frame_inputs,
            _resolution_input(),
            _aspect_ratio_input(ratios),
            _duration_input(),
            *_core_option_inputs(),
            *_extra_inputs(),
        ],
        outputs=_outputs(),
        hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
    )


async def generate_seedance1_video(
    cls,
    *,
    client,
    model,
    model_options,
    prompt,
    frames,
    resolution,
    aspect_ratio,
    duration,
    seed,
    camera_fixed,
    watermark,
    enable_offline_inference,
    generation_count,
    non_blocking,
):
    """
    Shared execute for the three nodes. `frames` is a list of (input name,
    image) for first_frame and, optionally, last_frame.
    """
    model_id = resolve_seedance1_model(model, model_options)
    validate_seedance1_prompt(prompt)

    helper = BytePlusVideoBase()
    for name, image in frames:
        validate_seedance1_frame(helper, name, image)
    images = [image for _name, image in frames]
    first_frame = images[0] if images else None
    last_frame = images[1] if len(images) > 1 else None
    content = build_seedance1_frame_content(helper, first_frame, last_frame)

    service_tier, execution_expires_after = helper._get_service_options(
        enable_offline_inference, VIDEO_DEFAULT_TIMEOUT
    )
    return await helper._common_generation_logic(
        client,
        prompt,
        duration,
        resolution,
        aspect_ratio,
        seed,
        generation_count,
        DEFAULT_FILENAME_PREFIX,  # unused: core-style nodes save nothing themselves
        False,
        non_blocking,
        cls.hidden.unique_id,
        model_name=model_id,
        content=content,
        forbidden_params=FORBIDDEN_PROMPT_FLAGS,
        service_tier=service_tier,
        execution_expires_after=execution_expires_after,
        extra_api_params={"camera_fixed": camera_fixed, "watermark": watermark},
        return_last_frame=True,
        node_class_type=cls.NODE_ID,
        workflow_prompt=cls.hidden.prompt,
        as_list=True,
    )


class BytePlusSeedanceTextToVideo(NonBlockingRerun, comfy_io.ComfyNode):
    """Core's ByteDance Text to Video, on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedanceTextToVideo"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return _schema(
            cls.NODE_ID,
            "BytePlus Seedance Text to Video",
            "Generate video using Seedance models on BytePlus ModelArk based on prompt",
            SEEDANCE_1_MODEL_OPTIONS,
            SEEDANCE_1_DEFAULT_MODEL,
            [],
            SEEDANCE_1_TEXT_RATIOS,
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        prompt,
        resolution,
        aspect_ratio,
        duration,
        seed=0,
        camera_fixed=False,
        watermark=False,
        enable_offline_inference=False,
        generation_count=1,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        return await generate_seedance1_video(
            cls,
            client=client,
            model=model,
            model_options=SEEDANCE_1_MODEL_OPTIONS,
            prompt=prompt,
            frames=[],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            enable_offline_inference=enable_offline_inference,
            generation_count=generation_count,
            non_blocking=non_blocking,
        )


class BytePlusSeedanceImageToVideo(NonBlockingRerun, comfy_io.ComfyNode):
    """Core's ByteDance Image to Video, on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedanceImageToVideo"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return _schema(
            cls.NODE_ID,
            "BytePlus Seedance Image to Video",
            "Generate video using Seedance models on BytePlus ModelArk based on image and prompt",
            SEEDANCE_1_MODEL_OPTIONS,
            SEEDANCE_1_DEFAULT_MODEL,
            [
                comfy_io.Image.Input(
                    "image",
                    tooltip="First frame to be used for the video.",
                ),
            ],
            SEEDANCE_1_IMAGE_RATIOS,
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        prompt,
        image,
        resolution,
        aspect_ratio,
        duration,
        seed=0,
        camera_fixed=False,
        watermark=False,
        enable_offline_inference=False,
        generation_count=1,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        return await generate_seedance1_video(
            cls,
            client=client,
            model=model,
            model_options=SEEDANCE_1_MODEL_OPTIONS,
            prompt=prompt,
            frames=[("image", image)],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            enable_offline_inference=enable_offline_inference,
            generation_count=generation_count,
            non_blocking=non_blocking,
        )


class BytePlusSeedanceFirstLastFrame(NonBlockingRerun, comfy_io.ComfyNode):
    """Core's ByteDance First-Last-Frame to Video, on BytePlus ModelArk."""

    NODE_ID = "BytePlusSeedanceFirstLastFrame"

    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return _schema(
            cls.NODE_ID,
            "BytePlus Seedance First-Last-Frame to Video",
            "Generate video using prompt and first and last frames.",
            SEEDANCE_1_FLF_MODEL_OPTIONS,
            SEEDANCE_1_FLF_DEFAULT_MODEL,
            [
                comfy_io.Image.Input(
                    "first_frame",
                    tooltip="First frame to be used for the video.",
                ),
                comfy_io.Image.Input(
                    "last_frame",
                    tooltip="Last frame to be used for the video.",
                ),
            ],
            SEEDANCE_1_IMAGE_RATIOS,
        )

    @classmethod
    async def execute(
        cls,
        client,
        model,
        prompt,
        first_frame,
        last_frame,
        resolution,
        aspect_ratio,
        duration,
        seed=0,
        camera_fixed=False,
        watermark=False,
        enable_offline_inference=False,
        generation_count=1,
        non_blocking=False,
    ) -> comfy_io.NodeOutput:
        return await generate_seedance1_video(
            cls,
            client=client,
            model=model,
            model_options=SEEDANCE_1_FLF_MODEL_OPTIONS,
            prompt=prompt,
            frames=[("first_frame", first_frame), ("last_frame", last_frame)],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            seed=seed,
            camera_fixed=camera_fixed,
            watermark=watermark,
            enable_offline_inference=enable_offline_inference,
            generation_count=generation_count,
            non_blocking=non_blocking,
        )


# Registered in __init__.py.
NODES = [
    BytePlusSeedanceTextToVideo,
    BytePlusSeedanceImageToVideo,
    BytePlusSeedanceFirstLastFrame,
]
