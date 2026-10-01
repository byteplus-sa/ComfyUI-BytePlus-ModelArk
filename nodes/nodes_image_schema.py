from comfy_api.latest import io as comfy_io
from .nodes_shared import BytePlusClientType
from .models_config import (
    SEEDREAM_4_MODEL_MAP,
    SEEDREAM_PRO,
    SEEDREAM_FLASH,
    SEEDREAM_LITE,
    SEEDREAM_4_5,
    SEEDREAM_4_0,
    SEEDREAM_MODEL_CAPS,
)
from .constants import (
    MAX_SEED,
    MIN_SEED,
    MAX_GENERATION_COUNT,
)

# --- BytePlus Seedream (nodes_seedream.py): ComfyUI core's size presets ---
# (label, width, height), copied from comfy_api_nodes/apis/bytedance.py.
_PRESETS_SEEDREAM_1K = [
    ("(1K) 1024x1024 (1:1)", 1024, 1024),
    ("(1K) 864x1152 (3:4)", 864, 1152),
    ("(1K) 1152x864 (4:3)", 1152, 864),
    ("(1K) 1312x736 (16:9)", 1312, 736),
    ("(1K) 736x1312 (9:16)", 736, 1312),
    ("(1K) 832x1248 (2:3)", 832, 1248),
    ("(1K) 1248x832 (3:2)", 1248, 832),
    ("(1K) 1568x672 (21:9)", 1568, 672),
]
_PRESETS_SEEDREAM_2K = [
    ("(2K) 2048x2048 (1:1)", 2048, 2048),
    ("(2K) 1728x2304 (3:4)", 1728, 2304),
    ("(2K) 2304x1728 (4:3)", 2304, 1728),
    ("(2K) 2848x1600 (16:9)", 2848, 1600),
    ("(2K) 1600x2848 (9:16)", 1600, 2848),
    ("(2K) 1664x2496 (2:3)", 1664, 2496),
    ("(2K) 2496x1664 (3:2)", 2496, 1664),
    ("(2K) 3136x1344 (21:9)", 3136, 1344),
]
_PRESETS_SEEDREAM_3K = [
    ("(3K) 3072x3072 (1:1)", 3072, 3072),
    ("(3K) 2592x3456 (3:4)", 2592, 3456),
    ("(3K) 3456x2592 (4:3)", 3456, 2592),
    ("(3K) 4096x2304 (16:9)", 4096, 2304),
    ("(3K) 2304x4096 (9:16)", 2304, 4096),
    ("(3K) 2496x3744 (2:3)", 2496, 3744),
    ("(3K) 3744x2496 (3:2)", 3744, 2496),
    ("(3K) 4704x2016 (21:9)", 4704, 2016),
]
_PRESETS_SEEDREAM_4K = [
    ("(4K) 4096x4096 (1:1)", 4096, 4096),
    ("(4K) 3520x4704 (3:4)", 3520, 4704),
    ("(4K) 4704x3520 (4:3)", 4704, 3520),
    ("(4K) 5504x3040 (16:9)", 5504, 3040),
    ("(4K) 3040x5504 (9:16)", 3040, 5504),
    ("(4K) 3328x4992 (2:3)", 3328, 4992),
    ("(4K) 4992x3328 (3:2)", 4992, 3328),
    ("(4K) 6240x2656 (21:9)", 6240, 2656),
]
# 1.5K sizes from the BytePlus docs (Seedream 5.0 Pro and Flash). Core lists
# them for Flash only; Pro gets them too, as in the ModelArk console. On Pro,
# 1.5K costs the same as 1K and gives better quality.
_PRESETS_SEEDREAM_1_5K = [
    ("(1.5K) 1536x1536 (1:1)", 1536, 1536),
    ("(1.5K) 1344x1792 (3:4)", 1344, 1792),
    ("(1.5K) 1792x1344 (4:3)", 1792, 1344),
    ("(1.5K) 2048x1152 (16:9)", 2048, 1152),
    ("(1.5K) 1152x2048 (9:16)", 1152, 2048),
    ("(1.5K) 1248x1872 (2:3)", 1248, 1872),
    ("(1.5K) 1872x1248 (3:2)", 1872, 1248),
    ("(1.5K) 2352x1008 (21:9)", 2352, 1008),
]
_PRESETS_SEEDREAM_5_FLASH = [
    ("(1K) 1024x1024 (1:1)", 1024, 1024),
    ("(1K) 864x1152 (3:4)", 864, 1152),
    ("(1K) 1152x864 (4:3)", 1152, 864),
    ("(1K) 1424x800 (16:9)", 1424, 800),
    ("(1K) 800x1424 (9:16)", 800, 1424),
    ("(1K) 832x1248 (2:3)", 832, 1248),
    ("(1K) 1248x832 (3:2)", 1248, 832),
    ("(1K) 1568x672 (21:9)", 1568, 672),
    *_PRESETS_SEEDREAM_1_5K,
    ("(2K) 2048x2048 (1:1)", 2048, 2048),
    ("(2K) 1776x2368 (3:4)", 1776, 2368),
    ("(2K) 2368x1776 (4:3)", 2368, 1776),
    ("(2K) 2816x1584 (16:9)", 2816, 1584),
    ("(2K) 1584x2816 (9:16)", 1584, 2816),
    ("(2K) 1664x2496 (2:3)", 1664, 2496),
    ("(2K) 2496x1664 (3:2)", 2496, 1664),
    ("(2K) 3136x1344 (21:9)", 3136, 1344),
]
SEEDREAM_CUSTOM_SIZE = "Custom"
SEEDREAM_PRESETS = {
    SEEDREAM_PRO: _PRESETS_SEEDREAM_1K + _PRESETS_SEEDREAM_1_5K + _PRESETS_SEEDREAM_2K,
    SEEDREAM_FLASH: _PRESETS_SEEDREAM_5_FLASH,
    SEEDREAM_LITE: _PRESETS_SEEDREAM_2K + _PRESETS_SEEDREAM_3K + _PRESETS_SEEDREAM_4K,
    SEEDREAM_4_5: _PRESETS_SEEDREAM_2K + _PRESETS_SEEDREAM_4K,
    SEEDREAM_4_0: _PRESETS_SEEDREAM_1K + _PRESETS_SEEDREAM_2K + _PRESETS_SEEDREAM_4K,
}


def seedream_adaptive_label(level):
    """Extra size preset that sends a ModelArk resolution level (e.g. "2K")."""
    return f"{level} (adaptive)"


def seedream_size_options(model):
    """
    size_preset options of a Seedream model: core's presets, then this pack's
    resolution levels ("2K (adaptive)", ...), then Custom (last, as in core).
    """
    return (
        [label for label, _, _ in SEEDREAM_PRESETS[model]]
        + [seedream_adaptive_label(level) for level in SEEDREAM_MODEL_CAPS[model]["adaptive_sizes"]]
        + [SEEDREAM_CUSTOM_SIZE]
    )

RECOMMENDED_SIZES_V4 = [
    "2K (adaptive)",
    "4K (adaptive)",
    "2048x2048 (1:1)",
    "2304x1728 (4:3)",
    "1728x2304 (3:4)",
    "2848x1600 (16:9)",
    "1600x2848 (9:16)",
    "2496x1664 (3:2)",
    "1664x2496 (2:3)",
    "3136x1344 (21:9)",
    "4096x4096 (1:1)",
    "4704x3520 (4:3)",
    "3520x4704 (3:4)",
    "5504x3040 (16:9)",
    "3040x5504 (9:16)",
    "4992x3328 (3:2)",
    "3328x4992 (2:3)",
    "6240x2656 (21:9)",
    "Custom",
]

RECOMMENDED_SIZES_V5 = [
    "2K (adaptive)",
    "3K (adaptive)",
    "4K (adaptive)",
    "2048x2048 (1:1)",
    "2304x1728 (4:3)",
    "1728x2304 (3:4)",
    "2848x1600 (16:9)",
    "1600x2848 (9:16)",
    "2496x1664 (3:2)",
    "1664x2496 (2:3)",
    "3136x1344 (21:9)",
    "3072x3072 (1:1)",
    "3456x2592 (4:3)",
    "2592x3456 (3:4)",
    "4096x2304 (16:9)",
    "2304x4096 (9:16)",
    "2496x3744 (2:3)",
    "3744x2496 (3:2)",
    "4704x2016 (21:9)",
    "Custom",
]

# Seedream 5.0 Pro: resolution levels and the documented width x height per level
RECOMMENDED_SIZES_V5_PRO = [
    "1K (adaptive)",
    "1.5K (adaptive)",
    "2K (adaptive)",
    "1024x1024 (1:1)",
    "1152x864 (4:3)",
    "864x1152 (3:4)",
    "1424x800 (16:9)",
    "800x1424 (9:16)",
    "1248x832 (3:2)",
    "832x1248 (2:3)",
    "1568x672 (21:9)",
    "1536x1536 (1:1)",
    "1792x1344 (4:3)",
    "1344x1792 (3:4)",
    "2048x1152 (16:9)",
    "1152x2048 (9:16)",
    "1872x1248 (3:2)",
    "1248x1872 (2:3)",
    "2352x1008 (21:9)",
    "2048x2048 (1:1)",
    "2368x1776 (4:3)",
    "1776x2368 (3:4)",
    "2816x1584 (16:9)",
    "1584x2816 (9:16)",
    "2496x1664 (3:2)",
    "1664x2496 (2:3)",
    "3136x1344 (21:9)",
    "Custom",
]


def get_image_generation_inputs(
    recommended_sizes,
    default_width=1024,
    default_height=1024,
    enable_group_generation=False,
):
    """
    Shared image generation inputs: size preset, custom width/height, seed,
    generation count and watermark.
    """
    inputs = [
        comfy_io.Combo.Input("size", options=recommended_sizes),
        comfy_io.Int.Input("width", default=default_width, min=1, max=8192),
        comfy_io.Int.Input("height", default=default_height, min=1, max=8192),
        comfy_io.Int.Input("seed", default=0, min=MIN_SEED, max=MAX_SEED),
    ]

    if enable_group_generation:
        inputs.extend(
            [
                comfy_io.Boolean.Input(
                    "enable_group_generation",
                    default=False,
                    tooltip="On=Group, Off=Single",
                ),
                comfy_io.Int.Input("max_images", default=1, min=1, max=15),
            ]
        )

    inputs.extend(
        [
            comfy_io.Int.Input(
                "generation_count", default=1, min=1, max=MAX_GENERATION_COUNT
            ),
            comfy_io.Boolean.Input("watermark", default=False),
        ]
    )

    return inputs
