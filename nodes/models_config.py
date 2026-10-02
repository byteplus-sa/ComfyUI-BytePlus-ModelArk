# BytePlus ModelArk and Seed Speech model catalog.
# UI names are the ModelArk model names; values are the dated model IDs sent
# to the API. To pick up a new model version, change the value only.

# Seedream 4
SEEDREAM_4_MODEL_MAP = {
    "seedream-4-5": "seedream-4-5-251128",
    "seedream-4-0": "seedream-4-0-250828",
}


# Seedream 5
SEEDREAM_5_MODEL_MAP = {
    "dola-seedream-5-0-pro": "dola-seedream-5-0-pro-260628",
    "dola-seedream-5-0-flash": "dola-seedream-5-0-flash-260915",
    "seedream-5-0-lite": "seedream-5-0-260128",
}

SEEDREAM_5_PRO_UI_MODEL = "dola-seedream-5-0-pro"
SEEDREAM_5_FLASH_UI_MODEL = "dola-seedream-5-0-flash"
SEEDREAM_5_LITE_UI_MODEL = "seedream-5-0-lite"

# Model ID -> regions where it must not be used. Seedream 5.0 Lite was
# deactivated in eu-west-1 on 2026-09-10 (model deprecation notice).
MODEL_REGION_EXCLUSIONS = {
    "seedream-5-0-260128": ("eu-west-1",),
}

SEEDREAM_LAYER_SIZES = ["auto", "1K", "1.5K", "2K"]

# Seedance video models
VIDEO_MODEL_MAP = {
    "seedance-1-0-pro": "seedance-1-0-pro-250528",
    "seedance-1-0-pro-fast": "seedance-1-0-pro-fast-251015",
    "dreamina-seedance-2-0": "dreamina-seedance-2-0-260128",
    "dreamina-seedance-2-0-fast": "dreamina-seedance-2-0-fast-260128",
    "dreamina-seedance-2-0-mini": "dreamina-seedance-2-0-mini-260615",
    "dreamina-seedance-2-5": "dreamina-seedance-2-5-260628",
    "dreamina-seedance-2-5-premium": "dreamina-seedance-2-5-premium-260915",
}

# Seedance 1.0 node options
VIDEO_1_UI_OPTIONS = [
    "seedance-1-0-pro",
    "seedance-1-0-pro-fast",
]

# Models BytePlus deprecated on 2026-09-15 and shuts down on 2026-11-11 (model
# deprecation notice): no node offers them. Video Query Tasks can still list
# their tasks. UI name -> (model ID, replacement).
RETIRED_MODELS = {
    "seedance-1-5-pro": ("seedance-1-5-pro-251215", "dreamina-seedance-2-0-mini-260615"),
}

# Retired Seedance models, listed in Video Query Tasks.
RETIRED_VIDEO_UI_OPTIONS = [name for name in RETIRED_MODELS if name.startswith("seedance-")]


# Seedance 2 node options
VIDEO_2_UI_OPTIONS = [
    "dreamina-seedance-2-0",
    "dreamina-seedance-2-0-fast",
    "dreamina-seedance-2-0-mini",
    "dreamina-seedance-2-5",
    "dreamina-seedance-2-5-premium",
]

SEEDANCE_2_5_UI_MODEL = "dreamina-seedance-2-5"
SEEDANCE_2_5_PREMIUM_UI_MODEL = "dreamina-seedance-2-5-premium"
# Models with Seedance 2.5 features: 30 s output, audio-only references,
# task_type, output_format and draft mode.
SEEDANCE_2_5_FAMILY = (SEEDANCE_2_5_UI_MODEL, SEEDANCE_2_5_PREMIUM_UI_MODEL)

VIDEO_2_MODEL_RESOLUTIONS = {
    "dreamina-seedance-2-0": ["480p", "720p", "1080p", "4k"],
    "dreamina-seedance-2-0-fast": ["480p", "720p"],
    "dreamina-seedance-2-0-mini": ["480p", "720p"],
    "dreamina-seedance-2-5": ["480p", "720p", "1080p"],
    "dreamina-seedance-2-5-premium": ["4k"],
}

VIDEO_2_MODEL_MAX_DURATIONS = {
    "dreamina-seedance-2-0": 15,
    "dreamina-seedance-2-0-fast": 15,
    "dreamina-seedance-2-0-mini": 15,
    "dreamina-seedance-2-5": 30,
    "dreamina-seedance-2-5-premium": 30,
}

# Per request: images, videos and audio clips (BytePlus: 50 references in total on
# Seedance 2.5 = 30 + 10 + 10, 15 on the 2.0 series = 9 + 3 + 3).
VIDEO_2_MODEL_REFERENCE_LIMITS = {
    "dreamina-seedance-2-0": {"images": 9, "videos": 3, "audios": 3},
    "dreamina-seedance-2-0-fast": {"images": 9, "videos": 3, "audios": 3},
    "dreamina-seedance-2-0-mini": {"images": 9, "videos": 3, "audios": 3},
    "dreamina-seedance-2-5": {"images": 30, "videos": 10, "audios": 10},
    "dreamina-seedance-2-5-premium": {"images": 30, "videos": 10, "audios": 10},
}

# Draft mode: the draft is always 480p; the final video generated from a draft
# task supports only these resolutions (verified against the live API: a
# Premium final at 1080p is rejected with InvalidResolutionParameter).
SEEDANCE_DRAFT_RESOLUTION = "480p"
SEEDANCE_DRAFT_FINAL_RESOLUTIONS = {
    "dreamina-seedance-2-5": ["1080p"],
    "dreamina-seedance-2-5-premium": ["4k"],
}

# Seedance 2.5 omni reference task types (omni_reference_task_type)
SEEDANCE_2_5_TASK_TYPES = ["auto", "reference", "edit", "extend"]
SEEDANCE_2_5_OUTPUT_FORMATS = ["mp4", "mov"]

# Model list for the task query node
# Retired models stay last so their task history can still be queried.
QUERY_TASKS_MODEL_LIST = ["all"] + VIDEO_1_UI_OPTIONS + VIDEO_2_UI_OPTIONS + RETIRED_VIDEO_UI_OPTIONS

# Visual understanding models
VISUAL_MODEL_MAP = {
    "dola-seed-2-1-turbo": "dola-seed-2-1-turbo-260628",
    "seed-2-0-pro": "seed-2-0-pro-260328",
    "seed-2-0-lite": "seed-2-0-lite-260428",
    "seed-2-0-mini": "seed-2-0-mini-260428",
}

# Seed Speech models (separate API key, see constants.SPEECH_REGION_BASE_URLS).
# Seed Audio 1.0: the model ID goes in the request body.
SEED_AUDIO_MODELS = ["seed-audio-1.0"]

# TTS: the UI name is the X-Api-Resource-Id header. Voice replication (ICL)
# resources are for cloned voices (speaker IDs starting with S_).
SEED_TTS_MODELS = ["seed-tts-2.0", "seed-tts-1.0", "seed-icl-2.0", "seed-icl-1.0"]
SEED_TTS_2_UI_MODEL = "seed-tts-2.0"

# ASR: UI name -> (mode, X-Api-Resource-Id). "fast" answers in one request
# (audio up to 2 h / 100 MB); "standard" submits a task and polls for the
# result (public audio URL only, up to 5 h / 512 MB).
SEED_ASR_MODELS = {
    "seed-asr-fast": ("fast", "volc.seedasr.auc_turbo"),
    "seed-asr-2.0": ("standard", "volc.seedasr.auc"),
    "seed-asr-1.0": ("standard", "volc.bigasr.auc"),
}
SEED_ASR_UI_OPTIONS = list(SEED_ASR_MODELS.keys())

# --- Nodes shaped like ComfyUI core's ByteDance nodes ---
# Option labels match core's; values are BytePlus model IDs (never core's IDs).

# Seedream (nodes_seedream.py)
SEEDREAM_PRO = "seedream 5.0 pro"
SEEDREAM_FLASH = "seedream 5.0 flash"
SEEDREAM_LITE = "seedream 5.0 lite"
SEEDREAM_4_5 = "seedream-4-5-251128"
SEEDREAM_4_0 = "seedream-4-0-250828"

# BytePlus Seedream: core's option label -> BytePlus model ID.
SEEDREAM_MODELS = {
    SEEDREAM_PRO: SEEDREAM_5_MODEL_MAP[SEEDREAM_5_PRO_UI_MODEL],
    SEEDREAM_FLASH: SEEDREAM_5_MODEL_MAP[SEEDREAM_5_FLASH_UI_MODEL],
    SEEDREAM_LITE: SEEDREAM_5_MODEL_MAP[SEEDREAM_5_LITE_UI_MODEL],
    SEEDREAM_4_5: SEEDREAM_4_MODEL_MAP["seedream-4-5"],
    SEEDREAM_4_0: SEEDREAM_4_MODEL_MAP["seedream-4-0"],
}

# Per model: reference-image cap (BytePlus docs: 14 on 5.0 Lite, 4.5 and 4.0;
# core caps 4.5 / 4.0 at 10) and custom width/height maximum, whether
# it batches (max_images, streamed b64) or returns one image by URL (pro/flash),
# prompt optimization options, custom-size pixel range, and the ModelArk
# resolution levels offered as extra "(adaptive)" size presets.
SEEDREAM_MODEL_CAPS = {
    SEEDREAM_PRO: {
        "max_refs": 10, "min_side": 240, "max_side": 8600, "batch": False,
        "fast": True, "thinking": True, "min_pixels": 921_600, "max_pixels": 4_624_220,
        "adaptive_sizes": ["1K", "1.5K", "2K"],
    },
    SEEDREAM_FLASH: {
        "max_refs": 10, "min_side": 240, "max_side": 8600, "batch": False,
        "fast": False, "thinking": False, "min_pixels": 921_600, "max_pixels": 4_624_220,
        "adaptive_sizes": ["1K", "1.5K", "2K"],
    },
    SEEDREAM_LITE: {
        "max_refs": 14, "min_side": 480, "max_side": 16384, "batch": True,
        "fast": False, "thinking": True, "min_pixels": 3_686_400, "max_pixels": 16_777_216,
        "adaptive_sizes": ["2K", "3K", "4K"],
    },
    SEEDREAM_4_5: {
        "max_refs": 14, "min_side": 480, "max_side": 16384, "batch": True,
        "fast": False, "thinking": True, "min_pixels": 3_686_400, "max_pixels": 16_777_216,
        "adaptive_sizes": ["2K", "4K"],
    },
    SEEDREAM_4_0: {
        "max_refs": 14, "min_side": 240, "max_side": 16384, "batch": True,
        "fast": False, "thinking": True, "min_pixels": 921_600, "max_pixels": 16_777_216,
        "adaptive_sizes": ["1K", "2K", "4K"],
    },
}
# Custom width / height follow the BytePlus size rule (total pixels in the
# model's range, aspect ratio 1:16 to 16:1), not core's 1024 minimum:
# min_side = ceil(sqrt(min_pixels / 16)), max_side = floor(sqrt(max_pixels * 16)),
# both even. The total-pixel and aspect checks run at execution.
SEEDREAM_MAX_ASPECT = 16
# Reference images plus generated images per request when batching.
SEEDREAM_MAX_TOTAL_IMAGES = 15

# BytePlus Seedream 5.0 Layer Separation: core's option label -> BytePlus model ID.
SEEDREAM_LAYER_SEPARATION_MODELS = {
    SEEDREAM_PRO: SEEDREAM_5_MODEL_MAP[SEEDREAM_5_PRO_UI_MODEL],
    SEEDREAM_FLASH: SEEDREAM_5_MODEL_MAP[SEEDREAM_5_FLASH_UI_MODEL],
}

# Seedance 1.x (nodes_seedance1.py)
# Core's labels are dated IDs. Core also offers seedance-1-5-pro-251215, which
# BytePlus shuts down on 2026-11-11.
SEEDANCE_1_MODELS = {
    "seedance-1-0-pro-250528": VIDEO_MODEL_MAP["seedance-1-0-pro"],
    "seedance-1-0-pro-fast-251015": VIDEO_MODEL_MAP["seedance-1-0-pro-fast"],
}
# Text to Video and Image to Video
SEEDANCE_1_MODEL_OPTIONS = list(SEEDANCE_1_MODELS)
SEEDANCE_1_DEFAULT_MODEL = "seedance-1-0-pro-fast-251015"
# First-Last-Frame to Video (1.0 Pro Fast has no last-frame support)
SEEDANCE_1_FLF_MODEL_OPTIONS = ["seedance-1-0-pro-250528"]
SEEDANCE_1_FLF_DEFAULT_MODEL = "seedance-1-0-pro-250528"
SEEDANCE_1_RESOLUTIONS = ["480p", "720p", "1080p"]
SEEDANCE_1_TEXT_RATIOS = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]
SEEDANCE_1_IMAGE_RATIOS = ["adaptive"] + SEEDANCE_1_TEXT_RATIOS
# BytePlus: 2-12 s for 1.0 Pro and Pro Fast (core's widget starts at 3).
SEEDANCE_1_MIN_DURATION = 2
SEEDANCE_1_MAX_DURATION = 12
SEEDANCE_1_DEFAULT_DURATION = 5

# Seedance 2 / 2.5 (nodes_seedance2.py)
# Model option label -> VIDEO_MODEL_MAP key. Core's labels plus the Premium
# pair; the Draft options render a 480p draft (SEEDANCE_DRAFT_RESOLUTION).
SEEDANCE2_CORE_MODEL_OPTIONS = {
    "Seedance 2.5": SEEDANCE_2_5_UI_MODEL,
    "Seedance 2.5 Draft": SEEDANCE_2_5_UI_MODEL,
    "Seedance 2.5 Premium": SEEDANCE_2_5_PREMIUM_UI_MODEL,
    "Seedance 2.5 Premium Draft": SEEDANCE_2_5_PREMIUM_UI_MODEL,
    "Seedance 2.0": "dreamina-seedance-2-0",
    "Seedance 2.0 Fast": "dreamina-seedance-2-0-fast",
    "Seedance 2.0 Mini": "dreamina-seedance-2-0-mini",
}
SEEDANCE2_CORE_DRAFT_OPTIONS = ("Seedance 2.5 Draft", "Seedance 2.5 Premium Draft")

# auto_downscale targets for reference videos (total pixels), per model and
# output resolution, from ComfyUI core's SEEDANCE2_REF_VIDEO_PIXEL_LIMITS. They
# are not API limits: BytePlus documents one range for every 2.x model and
# resolution (constants.REF_VIDEO_MIN/MAX_PIXELS), which is what is enforced.
# Unlisted pairs downscale to REF_VIDEO_MAX_PIXELS.
SEEDANCE2_REF_VIDEO_DOWNSCALE_TARGETS = {
    "dreamina-seedance-2-0": {"480p": 927_408, "720p": 927_408, "1080p": 2_073_600},
    "dreamina-seedance-2-0-fast": {"480p": 927_408, "720p": 927_408},
    "dreamina-seedance-2-0-mini": {"480p": 927_408, "720p": 927_408},
}

# Seed LLM (nodes_seed.py)
# Core's three labels first (core's first option is the default), then this
# pack's other Seed models, then the non-Seed LLMs ModelArk hosts. All accept
# images and video through the Responses API and support thinking.type
# enabled/disabled (checked live 2026-10-01 for DeepSeek V4.1 Flash and GLM 5.3 Flash,
# including reasoning.effort minimal/low/medium/high/max and Files API video).
SEED_LLM_MODEL_MAP = {
    "Seed 2.0 Pro": VISUAL_MODEL_MAP["seed-2-0-pro"],
    "Seed 2.0 Lite": VISUAL_MODEL_MAP["seed-2-0-lite"],
    "Seed 2.0 Mini": VISUAL_MODEL_MAP["seed-2-0-mini"],
    "Seed 2.1 Turbo": VISUAL_MODEL_MAP["dola-seed-2-1-turbo"],
    "DeepSeek V4.1 Flash": "deepseek-v4-1-flash-260910",
    "GLM 5.3 Flash": "glm-5-3-flash-260828",
}
SEED_LLM_UI_OPTIONS = list(SEED_LLM_MODEL_MAP.keys())
# Models that reject reasoning.effort (not in the ModelArk "Adjust
# chain-of-thought length" model table); the node does not send it for them.
# None of the current models (Seed 1.6 Flash was the last one; shut down 2026-11-11).
SEED_LLM_NO_REASONING_EFFORT = ()
SEED_LLM_MAX_IMAGES = 20
SEED_LLM_MAX_VIDEOS = 4
# Input modalities, checked live 2026-10-01 (a spoken word, a number in an image and in a video):
# every model above understands text, images (20 per request) and videos (4 per request).
# Audio is only understood by Seed 2.0 Lite and Mini (Responses API input_audio + file_id).
# Seed 2.0 Pro and Seed 2.1 Turbo reject it; DeepSeek V4.1 Flash and GLM 5.3 Flash accept
# it but answer that they cannot hear, so they get no audio input.
SEED_LLM_AUDIO_MODELS = ("Seed 2.0 Lite", "Seed 2.0 Mini")
# The docs only cap the total duration per request (120 minutes); 4 clips is this pack's slot count.
SEED_LLM_MAX_AUDIOS = 4
SEED_LLM_MAX_AUDIO_SECONDS = 120 * 60
