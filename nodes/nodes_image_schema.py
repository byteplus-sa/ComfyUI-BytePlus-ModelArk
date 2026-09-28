from comfy_api.latest import io as comfy_io
from .nodes_shared import BytePlusClientType
from .models_config import SEEDREAM_4_MODEL_MAP
from .constants import (
    MAX_SEED,
    MIN_SEED,
    MAX_GENERATION_COUNT,
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
