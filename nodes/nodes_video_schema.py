from comfy_api.latest import io as comfy_io
from .nodes_shared import BytePlusClientType, get_text, BytePlusException
from .models_config import (
    VIDEO_MODEL_MAP,
    VIDEO_1_UI_OPTIONS,
    VIDEO_1_5_UI_OPTIONS,
    VIDEO_2_UI_OPTIONS,
    VIDEO_2_MODEL_RESOLUTIONS,
    QUERY_TASKS_MODEL_LIST,
)
from .constants import (
    VIDEO_MAX_SEED,
    VIDEO_DEFAULT_TIMEOUT,
    VIDEO_MIN_TIMEOUT,
    VIDEO_MAX_TIMEOUT,
    VIDEO_FRAME_RATE,
    VIDEO_MIN_FRAMES,
    VIDEO_MAX_FRAMES,
    VIDEO_FRAME_STEP,
    VIDEO_BASE_FRAMES,
    VIDEO_RESOLUTIONS,
    DEFAULT_FILENAME_PREFIX,
)

ASPECT_RATIOS = ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]

def resolve_model_id(model_version: str) -> str:
    """
    Map the model name selected in the UI to its dated ModelArk model ID.
    """
    if model_version in VIDEO_MODEL_MAP:
        return VIDEO_MODEL_MAP[model_version]

    raise BytePlusException(f"Model ID not found for selection: {model_version}")

def resolve_query_models(model_version: str) -> list:
    """
    Model IDs to query in the task list node; [None] means all models.
    """
    target_models = []
    if model_version == "all":
        target_models = [None]
    elif model_version in VIDEO_MODEL_MAP:
        target_models.append(VIDEO_MODEL_MAP[model_version])
    else:
        target_models.append(model_version)
    
    return target_models

def _calculate_duration_and_frames_args(duration: float):
    """
    Choose the API duration or frames argument.
    Whole seconds use duration; fractional seconds are converted to frames.
    """
    if duration == int(duration):
        return ("duration", int(duration), int(duration))
    else:
        target_frames = duration * VIDEO_FRAME_RATE
        n = round((target_frames - VIDEO_BASE_FRAMES) / VIDEO_FRAME_STEP)
        final_frames = int(max(VIDEO_MIN_FRAMES, min(VIDEO_MAX_FRAMES, VIDEO_BASE_FRAMES + VIDEO_FRAME_STEP * n)))
        return ("frames", final_frames, int(round(final_frames / VIDEO_FRAME_RATE)))

def get_common_video_seed_inputs():
    return [
        comfy_io.Boolean.Input(
            "enable_random_seed",
            default=True,
            tooltip="On=Enabled, Off=Disabled",
        ),
        comfy_io.Int.Input("seed", default=0, min=0, max=VIDEO_MAX_SEED),
    ]

def get_common_video_runtime_inputs(include_offline=True):
    inputs = []
    if include_offline:
        inputs.append(comfy_io.Boolean.Input("enable_offline_inference", default=False))
    inputs.extend(
        [
        comfy_io.Int.Input("generation_count", default=1, min=1),
        comfy_io.String.Input("filename_prefix", default=DEFAULT_FILENAME_PREFIX),
        comfy_io.Boolean.Input("save_last_frame_batch", default=False),
        comfy_io.Boolean.Input("non_blocking", default=False),
        ]
    )
    return inputs

def get_duration_input(default=5.0, min_val=1.2, max_val=12.0, step=0.2, is_int=False):
    """
    Duration input definition.
    """
    if is_int:
        return comfy_io.Int.Input(
            "duration",
            default=int(default),
            min=int(min_val),
            max=int(max_val),
            display_mode=comfy_io.NumberDisplay.number,
        )
    else:
        return comfy_io.Float.Input(
            "duration",
            default=float(default),
            min=float(min_val),
            max=float(max_val),
            step=step,
            display_mode=comfy_io.NumberDisplay.number,
        )

def get_resolution_input(default="720p", support_1080p=True, support_4k=False, options=None):
    """
    Resolution input definition.
    """
    if options is None:
        options = ["480p", "720p"]
        if support_1080p:
            options.append("1080p")
        if support_4k:
            options.append("4k")
    else:
        options = list(options)
    
    if default not in options:
        default = options[-1]
        
    return comfy_io.Combo.Input("resolution", options=options, default=default)


def get_seedance2_resolutions(model_version: str) -> list[str]:
    return list(VIDEO_2_MODEL_RESOLUTIONS.get(model_version, ["480p", "720p"]))

def get_aspect_ratio_input(default="adaptive", include_adaptive=True):
    """
    Aspect ratio input definition.
    """
    options = list(ASPECT_RATIOS)
    if not include_adaptive:
        if "adaptive" in options:
            options.remove("adaptive")
        if default == "adaptive":
            default = "16:9"
            
    return comfy_io.Combo.Input("aspect_ratio", options=options, default=default)
