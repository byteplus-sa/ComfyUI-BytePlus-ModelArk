# BytePlus ModelArk and Seed Speech model catalog.
# UI names are the ModelArk model names; values are the dated model IDs sent
# to the API. To pick up a new model version, change the value only.

# Seedream 4
SEEDREAM_4_MODEL_MAP = {
    "seedream-4-5": "seedream-4-5-251128",
    "seedream-4-0": "seedream-4-0-250828",
}

SEEDREAM_4_0_UI_MODEL = "seedream-4-0"

# Prompt optimization modes (optimize_prompt_options.mode). "fast" is supported
# by Seedream 5.0 Pro and 4.0; 5.0 Flash, 5.0 Lite and 4.5 use "standard".
PROMPT_OPTIMIZATION_MODES = ["standard", "fast"]

# Seedream 5
SEEDREAM_5_MODEL_MAP = {
    "dola-seedream-5-0-pro": "dola-seedream-5-0-pro-260628",
    "dola-seedream-5-0-flash": "dola-seedream-5-0-flash-260915",
    "seedream-5-0-lite": "seedream-5-0-260128",
}

SEEDREAM_5_PRO_UI_MODEL = "dola-seedream-5-0-pro"
SEEDREAM_5_FLASH_UI_MODEL = "dola-seedream-5-0-flash"
SEEDREAM_5_LITE_UI_MODEL = "seedream-5-0-lite"
SEEDREAM_5_URL_MODELS = (SEEDREAM_5_PRO_UI_MODEL, SEEDREAM_5_FLASH_UI_MODEL)

# Seedream 5.0 Pro / Flash layer decomposition: one input image -> base image + up to 16 layers
SEEDREAM_LAYER_MODEL_MAP = {
    model: SEEDREAM_5_MODEL_MAP[model] for model in SEEDREAM_5_URL_MODELS
}
SEEDREAM_LAYER_SIZES = ["auto", "1K", "1.5K", "2K"]

# Seedance video models
VIDEO_MODEL_MAP = {
    "seedance-1-0-pro": "seedance-1-0-pro-250528",
    "seedance-1-0-pro-fast": "seedance-1-0-pro-fast-251015",
    "seedance-1-5-pro": "seedance-1-5-pro-251215",
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

# Seedance 1.5 node options
VIDEO_1_5_UI_OPTIONS = [
    "seedance-1-5-pro",
]

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
QUERY_TASKS_MODEL_LIST = ["all"] + VIDEO_1_UI_OPTIONS + VIDEO_1_5_UI_OPTIONS + VIDEO_2_UI_OPTIONS

# Visual understanding models
VISUAL_MODEL_MAP = {
    "dola-seed-2-1-turbo": "dola-seed-2-1-turbo-260628",
    "seed-2-0-pro": "seed-2-0-pro-260328",
    "seed-2-0-lite": "seed-2-0-lite-260428",
    "seed-2-0-mini": "seed-2-0-mini-260428",
    "seed-1-8": "seed-1-8-251228",
    "seed-1-6": "seed-1-6-250915",
    "seed-1-6-flash": "seed-1-6-flash-250715",
}
VISUAL_UI_OPTIONS = list(VISUAL_MODEL_MAP.keys())

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
