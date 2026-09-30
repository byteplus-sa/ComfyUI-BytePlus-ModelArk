# BytePlus ModelArk constants and console messages

# Data-plane endpoints per region. The first entry is the default.
REGION_BASE_URLS = {
    "ap-southeast-1": "https://ark.ap-southeast.bytepluses.com/api/v3",
    "eu-west-1": "https://ark.eu-west.bytepluses.com/api/v3",
}
DEFAULT_REGION = "ap-southeast-1"

# Asset library (virtual portraits / verified real people): signed OpenAPI,
# service "ark", separate host per region, IAM AK/SK instead of the API key.
ASSET_API_HOSTS = {
    "ap-southeast-1": "ark.ap-southeast-1.byteplusapi.com",
    "eu-west-1": "ark.eu-west-1.byteplusapi.com",
}
ASSET_API_VERSION = "2024-01-01"
ASSET_URI_PREFIX = "asset://"
ASSET_POLL_SECONDS = 3
ASSET_ACTIVE_TIMEOUT_SECONDS = 600

# Seed Speech (Seed Audio 1.0, TTS, ASR): a separate BytePlus product with its
# own API key (Seed Speech console -> Settings -> API Keys), sent as X-Api-Key.
# ModelArk keys are not accepted. Singapore is the only endpoint.
SPEECH_REGION_BASE_URLS = {
    "ap-southeast-1": "https://voice.ap-southeast-1.bytepluses.com",
}
DEFAULT_SPEECH_REGION = "ap-southeast-1"
SPEECH_API_KEY_ENV = "BYTEPLUS_SEED_SPEECH_API_KEY"
SPEECH_API_KEYS_CONSOLE_URL = "https://console.byteplus.com/voice/new/setting/apikeys"
SEED_AUDIO_PATH = "/api/v3/tts/create"
SEED_TTS_PATH = "/api/v3/tts/unidirectional"
SEED_ASR_FAST_PATH = "/api/v3/auc/bigmodel/recognize/flash"
SEED_ASR_SUBMIT_PATH = "/api/v3/auc/bigmodel/submit"
SEED_ASR_QUERY_PATH = "/api/v3/auc/bigmodel/query"
SEED_VOICE_CLONE_PATH = "/api/v3/tts/voice_clone"
SEED_VOICE_STATUS_PATH = "/api/v3/tts/get_voice"
# Fixed X-Api-App-Key value required by the TTS HTTP API.
SEED_TTS_APP_KEY = "aGjiRDfUWi"
# Seed Audio answers code 0; TTS/ASR end with 20000000.
SPEECH_SUCCESS_CODES = (0, 20000000)
SPEECH_ASR_PENDING_CODES = (20000001, 20000002)
SPEECH_ASR_SILENT_AUDIO_CODE = 20000003
SPEECH_REQUEST_TIMEOUT_SECONDS = 600
SPEECH_ASR_POLL_SECONDS = 2
SEED_AUDIO_MAX_PROMPT_CHARS = 3000
SEED_AUDIO_MAX_AUDIO_REFS = 3
SEED_AUDIO_REF_MAX_SECONDS = 30.0
SEED_AUDIO_REF_MAX_BYTES = 10 * 1024 * 1024
SEED_AUDIO_FORMATS = ["wav", "mp3", "ogg_opus", "pcm"]
# Raw PCM output (16-bit mono) defaults to 40 kHz, like wav.
SEED_AUDIO_PCM_DEFAULT_RATE = 40000
SEED_AUDIO_SAMPLE_RATES = ["default", "8000", "16000", "24000", "32000", "44100", "48000"]
SEED_TTS_SAMPLE_RATES = ["24000", "16000", "8000", "22050", "32000", "44100", "48000"]
SEED_TTS_2_SAMPLE_RATES = ("24000", "16000", "8000")
# explicit_language values of the TTS API ("auto" sends nothing).
SEED_TTS_LANGUAGES = [
    "auto", "en", "zh-cn", "ja", "ko", "id", "th", "vi", "ms", "fil", "es", "es-mx",
    "pt", "pt-br", "de", "fr", "it", "ru", "pl", "tr", "sv", "ar",
]
# ASR audio.language values ("auto" sends nothing: Chinese, English and Chinese
# dialects are recognized without a language).
SEED_ASR_LANGUAGES = [
    "auto", "en-US", "zh-CN", "yue-CN", "ja-JP", "ko-KR", "id-ID", "ms-MY", "th-TH",
    "vi-VN", "fil-PH", "hi-IN", "bn-BD", "ur-PK", "pa-PK", "km-KH", "my-MM", "es-MX",
    "pt-BR", "de-DE", "fr-FR", "it-IT", "nl-NL", "pl-PL", "ro-RO", "ru-RU", "uk-UA",
    "tr-TR", "el-GR", "cs-CZ", "da-DK", "fi-FI", "hu-HU", "no-NO", "sv-SE", "bg-BG",
    "ar-SA", "az-AZ", "kk-KZ", "sw-KE",
    # Standard models (seed-asr-2.0 / 1.0) only:
    "sk-SK", "sl-SI", "hr-HR", "sr-RS", "lt-LT", "lv-LV", "et-EE", "fa-IR", "af-ZA",
    "is-IS", "sq-AL", "ca-ES", "ceb-PH", "ga-IE", "jv-ID", "uz-UZ",
]
SEED_ASR_STANDARD_ONLY_LANGUAGES = (
    "sk-SK", "sl-SI", "hr-HR", "sr-RS", "lt-LT", "lv-LV", "et-EE", "fa-IR", "af-ZA",
    "is-IS", "sq-AL", "ca-ES", "ceb-PH", "ga-IE", "jv-ID", "uz-UZ",
)
# Hotwords and dialogue context only work with the Chinese-English model.
SEED_ASR_CONTEXT_LANGUAGES = ("auto", "zh-CN")
# audio.format for URL input. The fast mode accepts only raw/wav/mp3/ogg; the
# standard mode also pcm/spx/amr/aac/m4a (and requires the field).
SEED_ASR_AUDIO_FORMATS = ["auto", "wav", "mp3", "ogg", "m4a", "aac", "amr", "spx", "pcm", "raw"]
SEED_ASR_FAST_FORMATS = ("raw", "wav", "mp3", "ogg")
SEED_ASR_EXTENSION_FORMATS = {
    "wav": "wav", "mp3": "mp3", "ogg": "ogg", "opus": "ogg", "oga": "ogg", "m4a": "m4a",
    "aac": "aac", "amr": "amr", "spx": "spx", "pcm": "pcm", "raw": "raw",
}
# Standard-mode results arrive within 3 hours.
SEED_ASR_MAX_WAIT_SECONDS = 12600
# context_language: reference language for Western European text.
SEED_TTS_CONTEXT_LANGUAGES = ["default", "id", "es", "pt"]
SEED_TTS_DEFAULT_UNSUPPORTED_CHAR_RATIO = 0.3
SEED_ASR_ZH_VARIANTS = ["none", "traditional", "tw", "hk"]
SEED_ASR_SAMPLE_RATE = 16000
SEED_ASR_FAST_MAX_BYTES = 100 * 1024 * 1024
# ASR 2.0 visual context image limit.
SEED_ASR_CONTEXT_IMAGE_MAX_PIXELS = 768 * 768

# Voice Replication 2.0 (voice cloning for TTS / Seed Audio)
SEED_VOICE_CLONE_MAX_BYTES = 10 * 1024 * 1024
SEED_VOICE_CLONE_LANGUAGES = {
    "en": 1, "zh": 0, "ja": 2, "es": 3, "id": 4, "pt": 5, "de": 6, "fr": 7, "ko": 8, "it": 9,
    "th": 10, "vi": 11, "ru": 12, "fil": 13, "ms": 14, "ar": 15, "mx": 16, "pt-br": 17,
    "pl": 19, "tr": 20, "sv": 21,
}
# Training status: 0 NotFound, 1 Training, 2 Success, 3 Failed, 4 Active.
SEED_VOICE_READY_STATUSES = (2, 4)
SEED_VOICE_FAILED_STATUS = 3
SEED_VOICE_NOT_FOUND_STATUS = 0
SEED_VOICE_POLL_SECONDS = 2
SEED_VOICE_TRAINING_TIMEOUT_SECONDS = 600
# Postpaid custom voice IDs must not match this (reserved prefixes/suffixes, format).
SEED_CUSTOM_VOICE_ID_REJECT = (
    r"^((?i:S_|ICL_|MIX_|DiT_|BV)|[a-z]{2}_|(?i:(wvae|moon|mercury|venus|earth|mars|jupiter|saturn"
    r"|uranus|neptune|pluto|umm)_)).*|.*_(?i:bigtts|bigtts_cc|tob|cs_tob|streaming)$|^[^a-zA-Z]"
    r"|.*[-_]$|^.{0,7}$|^.{257,}$|.*[^a-zA-Z0-9_-].*"
)
# Uploads of connected media to Comfy.org storage (for APIs that need a URL).
SPEECH_UPLOAD_CACHE_TTL_SECONDS = 43200
SPEECH_UPLOAD_CACHE_MAX_ENTRIES = 128

# Seed Speech error codes without a dedicated MESSAGES key (Voice Replication).
SPEECH_ERROR_TEXT = {
    45001001: "Invalid request parameters.",
    45001101: "Audio upload failed. Check the audio format and size.",
    45001102: "Transcription of the reference audio failed. Use a clearer recording.",
    45001104: "Voiceprint check failed. Try a different sample or speaker.",
    45001105: "Could not read the audio data.",
    45001107: "Speaker ID not found. Check the voice slot ID in the Seed Speech console.",
    45001108: "Audio transcoding failed. Use a different sample.",
    45001109: "The reference audio does not match reference_text.",
    45001112: "The reference audio is too noisy (SNR check failed).",
    45001113: "Denoising failed. Use a different sample.",
    45001114: "The reference audio quality is too low.",
    45001122: "No speech was found in the reference audio.",
    45001123: "This voice slot has no training attempts left (15 per slot). Use another speaker ID.",
    45001124: "The reference audio content was rejected by review.",
    45001125: "demo_text was rejected by review.",
    45001126: "demo_text must be 4 to 80 characters.",
    45001127: "The reference audio was rejected by review.",
    45001128: "The reference audio text was rejected by review.",
    55001307: "Voice cloning failed on the server. Try again.",
}

# General
MAX_SEED = 2147483647
MIN_SEED = -1
DEFAULT_GUIDANCE_SCALE = 5.0
MAX_GENERATION_COUNT = 2048

# Image resolution limits
MIN_IMAGE_PIXELS_V4_5 = 3686400
MAX_IMAGE_PIXELS_V4 = 4096 * 4096
MIN_IMAGE_PIXELS_V5 = 3686400
MAX_IMAGE_PIXELS_V5 = 4096 * 4096
MIN_IMAGE_PIXELS_V5_PRO = 1280 * 720
MAX_IMAGE_PIXELS_V5_PRO = 4624220  # 2048 x 2048 x 1.1025
MIN_LAYER_INPUT_PIXELS = 512 * 512
MAX_LAYER_INPUT_PIXELS = 6000 * 6000
MIN_ASPECT_RATIO = 1.0 / 16.0
MAX_ASPECT_RATIO = 16.0

# Video limits
VIDEO_MAX_SEED = 2147483647
VIDEO_DEFAULT_TIMEOUT = 172800
VIDEO_MIN_TIMEOUT = 3600
VIDEO_MAX_TIMEOUT = 259200
IMAGE_MIN_EDGE = 300
IMAGE_MAX_EDGE = 6000
IMAGE_MIN_RATIO = 0.4
IMAGE_MAX_RATIO = 2.5
REF_IMAGE_MAX_SIZE_MB = 30.0
REF_IMAGE_MAX_TOTAL_REQUEST_MB = 64.0
REF_MEDIA_MIN_DURATION = 1.8
REF_MEDIA_MAX_DURATION = 15.2
REF_MEDIA_MAX_DURATION_SEEDANCE_2_5 = 30.2
REF_VIDEO_MIN_DURATION = REF_MEDIA_MIN_DURATION
REF_VIDEO_MAX_DURATION = REF_MEDIA_MAX_DURATION
REF_VIDEO_MAX_TOTAL_DURATION = REF_MEDIA_MAX_DURATION
REF_VIDEO_MAX_SIZE_MB = 200.0
REF_VIDEO_MIN_PIXELS = 409600
REF_VIDEO_MAX_PIXELS = 8295044
REF_VIDEO_MIN_FPS = 24.0
REF_VIDEO_MAX_FPS = 60.0
SEEDANCE_REQUEST_MAX_BYTES = 64 * 1024 * 1024
REF_AUDIO_MIN_DURATION = REF_MEDIA_MIN_DURATION
REF_AUDIO_MAX_DURATION = REF_MEDIA_MAX_DURATION
REF_AUDIO_MAX_TOTAL_DURATION = REF_MEDIA_MAX_DURATION
REF_AUDIO_MAX_SIZE_MB = 15.0
REF_AUDIO_MAX_TOTAL_REQUEST_MB = 64.0
DEFAULT_VISUAL_SYSTEM_PROMPT = "You are a helpful assistant that describes and analyzes images and videos accurately."
DEFAULT_VISUAL_USER_PROMPT = "Describe the content of this image or video."
VIDEO_FRAME_RATE = 24.0
VIDEO_MIN_FRAMES = 29
VIDEO_MAX_FRAMES = 289
VIDEO_FRAME_STEP = 4.0
VIDEO_BASE_FRAMES = 25.0
VIDEO_RESOLUTIONS = ["480p", "720p", "1080p", "4k"]
# Approximate pixel count per video resolution
VIDEO_RESOLUTION_PIXELS = {
    "480p": 409920,
    "720p": 921600,
    "1080p": 2073600,
    "4k": 8294400,
}
DEFAULT_FILENAME_PREFIX = "BytePlus/Video/Batch/Seedance"

MESSAGES = {
    "init_sdk_ver_low": "BytePlus SDK {current} is older than the required {min}; the BytePlus nodes are disabled. Update it, then restart ComfyUI:\n  {cmd}",
    "init_sdk_not_found": "BytePlus SDK is not installed; the BytePlus nodes are disabled. Install the requirements, then restart ComfyUI:\n  {cmd}",
    "init_sdk_version_unknown": "Could not read the BytePlus SDK version (no package metadata); loading anyway. The nodes need byteplus-python-sdk-v2 >= {min}.",
    "init_dep_check_err": "Dependency check error; the BytePlus nodes are disabled: {e}",
    "err_file_upload_failed": "File upload to ModelArk failed: {e}",
    "err_file_no_id": "File upload to ModelArk returned no file ID. Try again.",
    "err_files_api_missing": "This BytePlus SDK has no Files API; update byteplus-python-sdk-v2.",
    "err_file_processing_failed": "ModelArk could not process file {id}: {reason}",
    "err_file_processing_timeout": "File {id} was not ready after {seconds} s. Try again, or use a smaller file.",
    "err_file_status_check": "Could not check the status of file {id}: {e}",
    "api_file_not_found": "Info: API keys file not found. Please rename 'api_keys.json.example' to 'api_keys.json' and fill in your keys.",
    "api_file_empty": "Warning: 'api_keys.json' is empty or not formatted correctly.",
    "api_load_error": "Error: Failed to load 'api_keys.json': {e}",
    "api_key_not_found": "Error: API Key for '{key_name}' not found.",
    "est_fallback": "Fallback Default",
    "est_history": "History Average",
    "est_regression": "Linear Regression",
    "est_recent": "Recent Load Adjustment",
    "task_submitted_est": "Task submitted. Est. time: {time}s (Method: {method})",
    "task_info_simple": "Task ID: {task_id} | Model: {model}",
    "progress_non_blocking_submitted": "Submitted task(s): {task_ids}",
    "progress_non_blocking_pending": "Task(s) still processing: {task_ids}",
    "batch_submit_start": "Submitting batch of {count} tasks (Model: {model})...",
    "batch_submit_result": "Submission complete. Created: {created}, Failed: {failed}.",
    "batch_failed_summary": "⚠️ {count} tasks failed to create. Reason summary:",
    "batch_failed_reason": "  - {msg}: {count} times",
    "debug_create_request_params": "Create request params: {params}",
    "log_raw_api_response": "Raw API response: {raw}",
    "upload_ref_video_start": "Uploading reference video (done: {done}, pending: {pending})...",
    "upload_ref_video_done": "Reference video upload completed.",
    "upload_ref_video_cache_hit": "Reference video cache hit; reused uploaded result.",
    
    # Updated
    "polling_single": "Task {task_id}: Running... {elapsed}s / {max}s elapsed",
    "polling_single_waiting": "Task {task_id}: Queued and waiting for resources... (Status: {status})",
    "polling_batch_stats": "Batch Progress: {done}/{total} done. {pending} pending... (Elapsed {elapsed}s / {max}s) [Run: {running}, Queue: {queued}]",

    "interrupted": "\n" + "Processing interrupted by user. Cancelling pending tasks...",
    "cancel_task_success": "Successfully cancelled task: {task_id}",
    "cancel_task_failed": "Failed to cancel task {task_id}: {msg}",
    "cancel_batch_summary": "Batch cancellation finished. Success: {success}, Failed: {failed}.",
    "cancel_batch_reason": "  - {msg}",
    "task_finished_single": "Task completed successfully.",
    "batch_finished_stats": "Batch finished. Success: {success}, Failed: {failed}.",
    "batch_handling": "Handling {count} successful tasks. Sorting by seed and downloading...",
    "batch_copying": "Copying files to output directory: {path}",
    "err_download_url": "Async download failed, URL: {url}, Error: {e}",
    "check_status": "Checking status of {count} pending task(s)...",
    "err_create_dummy_video": "Failed to create placeholder video: {e}",
    "err_on_tasks_created": "Failed to record created task IDs: {e}",
    "err_task_create": "Task creation failed: {e}",
    "err_task_check": "Failed to check status for {tid}: {e}",
    "err_task_fail_msg": "Task {tid} failed: {msg}",
    "err_batch_fail_all": "Batch failed: No tasks succeeded.",
    "err_copy_fail": "Failed to copy file: {path}. Error: {e}",
    "err_convert_tensor": "Failed to convert frame to tensor: {e}",
    "err_check_status_batch": "API Error checking batch status: {e}",
    "err_task_fail_ignored": "⚠️ Node {node_id} task failed (Ignored in concurrent mode): {msg}",
    "debug_node_count": "Debug: Detected {count} {type} nodes.",
    "download_retry": "Warning: Download failed (Attempt {attempt}/{total}). Retrying in {delay}s... Error: {e}",
    "stream_recv_image": "Streaming: Image {index} generated.",
    "stream_partial_fail": "Streaming Warning: Image {index} failed: {msg}",
    "popup_req_failed": "Request failed: {msg}",
    "popup_task_failed": "Task {task_id} failed: {msg}",
    "popup_batch_pending": "Batch ({count} tasks) is pending. Run again to check results.",
    "popup_task_pending": "Task {task_id} is {status}. Run again to check results.",
    "popup_param_not_allowed": "Parameter Error: Parameter '--{param}' is not allowed in the prompt. Please use the node's widget for this value.",
    "popup_first_frame_missing": "Parameter Error: A first frame image must be provided when using a last frame image.",
    "popup_ref_missing": "Parameter Error: At least one reference image must be provided.",
    "popup_audio_invalid": "Parameter Error: Invalid reference audio input. Please provide a valid ComfyUI audio object.",
    "popup_video_prompt_or_ref_required": "Parameter Error: A prompt or at least one reference input is required.",
    "popup_audio_requires_visual_ref": "Parameter Error: Audio reference requires at least one reference image or reference video.",
    "popup_first_last_conflict_with_refs": "Parameter Error: First/last frame mode cannot be used together with reference inputs. Please choose one.",
    "popup_ref_count_exceeded": "Parameter Error: Model {model} supports at most {max} {kind} inputs. Current: {count}",
    "ref_kind_images": "reference image",
    "ref_kind_videos": "reference video",
    "ref_kind_audios": "reference audio",
    "popup_ref_image_hw_out_of_range": "Parameter Error: Reference image width/height must be between {min}px and {max}px. Current: {width}x{height}",
    "popup_ref_image_ratio_out_of_range": "Parameter Error: Reference image ratio must be between {min} and {max}. Current: {ratio}",
    "popup_ref_image_size_exceeded": "Parameter Error: Single reference image Base64 size cannot exceed {max_mb}MB. Current: {size_mb}MB",
    "popup_ref_image_total_size_exceeded": "Parameter Error: Total reference image Base64 size cannot exceed {max_mb}MB. Current: {size_mb}MB",
    "popup_ref_video_invalid": "Parameter Error: Invalid reference video input. Please provide a valid video object.",
    "popup_ref_video_url_format": "Parameter Error: Reference video URL must be mp4 or mov.",
    "popup_ref_video_format": "Parameter Error: Reference video format must be mp4 or mov. Current: {fmt}",
    "popup_ref_video_hw_out_of_range": "Parameter Error: Reference video width/height must be between {min}px and {max}px. Current: {width}x{height}",
    "popup_ref_video_ratio_out_of_range": "Parameter Error: Reference video ratio must be between {min} and {max}. Current: {ratio}",
    "popup_ref_video_pixels_out_of_range": "Parameter Error: Reference video pixel count must be between {min} and {max}. Current: {pixels}",
    "popup_ref_video_duration_out_of_range": "Parameter Error: Single reference video duration must be between {min}s and {max}s. Current: {duration}s",
    "popup_ref_video_total_duration_exceeded": "Parameter Error: Total reference video duration cannot exceed {max}s. Current: {duration}s",
    "popup_ref_video_size_exceeded": "Parameter Error: Single reference video size cannot exceed {max_mb}MB. Current: {size_mb}MB",
    "popup_ref_video_fps_out_of_range": "Parameter Error: Reference video frame rate must be between {min} and {max} FPS. Current: {fps} FPS",
    "popup_ref_video_codec_unsupported": "Parameter Error: Reference video codec must be H.264 or H.265. Current: {codec}",
    "popup_ref_audio_codec_unsupported": "Parameter Error: Audio codec in the reference video must be AAC or MP3. Current: {codec}",
    "popup_ref_audio_duration_out_of_range": "Parameter Error: Single reference audio duration must be between {min}s and {max}s. Current: {duration}s",
    "popup_ref_audio_total_duration_exceeded": "Parameter Error: Total reference audio duration cannot exceed {max}s. Current: {duration}s",
    "popup_ref_audio_size_exceeded": "Parameter Error: Single reference audio size cannot exceed {max_mb}MB. Current: {size_mb}MB",
    "popup_ref_audio_total_size_exceeded": "Parameter Error: Total reference audio request size cannot exceed {max_mb}MB. Current: {size_mb}MB",
    "popup_prepare_failed": "Failed to prepare task: {e}",
    "err_pixels_range": "Parameter Error: Total pixels must be between {min} and {max}. Your current: {current}",
    "err_aspect_ratio": "Parameter Error: Aspect ratio must be between {min} and {max}. Your current: {current}",
    "err_download_img": "Error: Failed to download the generated image.",
    "err_model_not_supported": "This node does not support model {model}.",
    "err_seedream_flash_prompt_optimization": "Seedream 5.0 Flash supports only standard prompt optimization.",
    "err_seedance2_resolution_unsupported": "Model {model} does not support {resolution}. Supported resolutions: {supported}.",
    "err_seedance2_duration_unsupported": "Model {model} requires a duration between {min} and {max} seconds. Current: {duration}",
    "err_seedance25_editing_params": "Seedance 2.5 video editing (task_type edit) requires the adaptive aspect ratio and auto duration.",
    "err_no_draft_to_reuse": "reuse_last_draft_task is on, but this node has no draft to reuse (drafts are remembered per node and model until ComfyUI restarts). Turn off reuse and paste the draft task ID into draft_task_id, or generate a draft first.",
    "err_draft_final_resolution": "Final videos rendered from a {model} draft support only {supported}. Current resolution: {resolution}.",
    "err_seedance25_first_frame_ratio": "Seedance 2.5 image-to-video keeps the first frame's aspect ratio. Set aspect_ratio to adaptive.",
    "err_seedance25_task_type_needs_video": "Seedance 2.5 task_type '{task_type}' needs at least one reference video.",
    "err_seedance25_extend_params": "Seedance 2.5 video extension requires the adaptive aspect ratio.",
    "err_asset_credentials_missing": "The asset library needs IAM AK/SK with asset-library permission (plus Dreamina Seedance Advanced Creation Rights on the account). Add \"accessKey\" and \"secretKey\" (and \"sessionToken\" for STS keys) to the selected entry in api_keys.json, or set BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY, then restart ComfyUI.",
    "err_asset_api": "Asset library {action} failed: {code}: {message}{hint}",
    "hint_asset_auth": " Check the AK/SK in api_keys.json or the BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY environment variables.",
    "hint_asset_denied": " The IAM user needs asset-library permission in this project, and the account needs Dreamina Seedance Advanced Creation Rights.",
    "hint_asset_throttled": " CreateAsset is rate-limited by your Advanced Creation Rights tier (Entry 3, Advanced 120, Premium 300 per minute); wait and retry.",
    "err_asset_no_id": "Asset library {action} returned no ID. Try again; if it repeats, check the asset library in the ModelArk console.",
    "err_asset_source_missing": "Connect an image or set image_url (a public HTTPS URL) for the asset.",
    "err_asset_url_invalid": "image_url must be an HTTPS URL. Current: {url}",
    "err_asset_group_ambiguous": "{count} asset groups are named '{name}'. Set group_id to choose one.",
    "err_asset_failed": "Asset {asset_id} failed processing or review (status {status}). Check the material and try another image.",
    "err_asset_timeout": "Asset {asset_id} is still {status} after {seconds}s. It may still become Active; list it with Asset Library before creating it again.",
    "err_asset_uri_invalid": "Not a valid reference: '{value}'. Use an https:// URL or asset://<asset_id>, one per line.",
    "err_comfy_image_upload_unavailable": "Local images are uploaded through Comfy.org storage to get the HTTPS URL CreateAsset needs, which is unavailable in this ComfyUI ({e}). Pass a public image_url instead.",
    "err_comfy_image_upload_failed": "Uploading the image to Comfy.org storage failed: {e}. Log in to your Comfy.org account in ComfyUI (or set a Comfy.org API key), or pass a public image_url instead.",
    "asset_group_created": "Created asset group {group_id} ('{name}').",
    "asset_created": "Created asset {asset_id}; waiting for it to become Active...",
    "asset_status": "Asset {asset_id}: {status}",
    "asset_reused": "Reusing asset {asset_id} created for the same image in this session.",
    "err_comfy_upload_unavailable": "Reference videos are uploaded through Comfy.org storage, which is unavailable in this ComfyUI ({e}). Update ComfyUI, remove --disable-api-nodes, or pass a public mp4/mov link in ref_video_urls instead.",
    "err_comfy_upload_failed": "Uploading the reference video to Comfy.org storage failed: {e}. Log in to your Comfy.org account in ComfyUI (or set a Comfy.org API key), or pass a public mp4/mov link in ref_video_urls instead.",
    "err_request_body_too_large": "The final request body exceeds the 64 MiB limit (maximum {max_bytes} bytes; current {current_bytes} bytes). Reduce reference media.",
    "err_transparent_needs_one_image": "Transparent background needs exactly one reference image with an alpha channel (connect its mask to reference_mask). Current reference images: {n}.",
    "err_transparent_needs_png": "Transparent background returns PNG; set output_format to png.",
    "err_layer_input_pixels": "Layer decomposition input must be between {min} and {max} total pixels. Current: {current}.",
    "err_layer_decomposition_empty": "Layer decomposition returned no images.",
    "err_gen_model": "Failed to generate image with model {model}: {e}",
    "err_img_limit_10": "Parameter Error: The number of input images cannot exceed 10.",
    "err_img_limit_15": "Parameter Error: The sum of input images ({n}) and max generated images ({max}) cannot exceed 15.",
    "err_img_limit_group_15": "Parameter Error: The sum of input images ({n}) and max generated images ({max}) cannot exceed 15 in group mode (Total: {total}).",
    "popup_key_valid_err": "Config Error: Selected key '{key}' is invalid or not found. Please check api_keys.json.",
    "err_new_key_empty": "Config Error: Manual entry enabled but API Key is empty.",
    "err_new_key_invalid": "Auth Failed: Input API Key is invalid. Connection rejected by server.",
    "info_new_key_saved": "Info: New key '{name}' verified and saved to api_keys.json.",
    "quota_exceeded": "Quota Exceeded: Usage limit for model {model} reached ({used}/{limit}). Estimated cost: {estimated}. Limit has been automatically removed. Please run again or set a new quota.",
    "quota_update_failed": "Warning: Failed to update quota usage: {e}",
    "quota_set_log": "Set quota for {model}: {limit} ({type})",
    "quota_update_log": "Updated usage for {model}: +{cost} (Total: {total})",

    # Visual Understanding
    "visual_processing_input": "Processing visual_input_{i}, type: {type}",
    "visual_found_file": "Found file in input directory: {path}",
    "visual_uploading": "Uploading file: {path}",
    "visual_uploaded": "Uploaded file_id: {id}, Status: {status}",
    "visual_wait_active": "Waiting for file {id} to be active...",
    "visual_file_status": "File {id} status: {status}",
    "visual_new_conv": "Starting new conversation.",
    "visual_cont_conv": "Continuing conversation {id}...",
    "visual_cached_id": "Cached response_id for next turn: {id}",
    "visual_stream_start": "Starting Streaming Response...",
    "visual_stream_complete": "Stream Completed.",
    "visual_task_created": "Response Task Created",
    "visual_polling": "Polling Response Task: {id}",
    "visual_task_complete": "Task {id} completed.",
    "visual_task_failed": "Task {id} failed: {msg}",

    # Seed Speech (Seed Audio, TTS, ASR)
    "speech_key_empty": "Paste a Seed Speech API key into new_api_key, or pick a saved key.",
    "speech_key_not_found": "Seed Speech API key '{key_name}' was not found in speech_api_keys.json.",
    "speech_env_key_missing": "The environment variable {env} is not set. Set it to your Seed Speech API key, or pick another key.",
    "speech_key_save_failed": "Could not write speech_api_keys.json; key '{name}' was not saved and stays in the node.",
    "speech_key_saved": "Seed Speech API key '{name}' saved to speech_api_keys.json.",
    "speech_wrong_client": "Connect a BytePlus Speech Client. Seed Speech needs its own API key; the ModelArk API Client does not work here.",
    "speech_request_failed": "Seed Speech {operation} failed (HTTP {status}, code {code}): {message}{logid}",
    "speech_network_error": "Could not reach Seed Speech ({operation}): {e}",
    "speech_timeout": "Seed Speech {operation} timed out after {seconds} s.",
    "speech_err_auth": "Invalid Seed Speech API key. Create one in the Seed Speech console ({url}) and activate the service there; ModelArk API keys do not work with Seed Speech.",
    "speech_err_speaker": "The voice is not available for this key or model. Check the speaker ID and that the model matches it (TTS 2.0 voices need seed-tts-2.0, cloned voices seed-icl-*).",
    "speech_err_text_limit": "The text is longer than the model accepts.",
    "speech_err_busy": "Seed Speech is busy. Try again later.",
    "speech_err_concurrency": "Seed Speech concurrency limit reached. Wait for running requests to finish, then try again.",
    "speech_err_params": "Invalid request parameters",
    "speech_err_empty_input_audio": "The input audio is empty.",
    "speech_err_audio_format": "The audio format is not supported.",
    "speech_empty_audio": "Seed Speech {operation} returned no audio.",
    "speech_audio_decode_failed": "Could not decode the audio returned by Seed Speech: {e}",
    "speech_audio_invalid": "Invalid audio input. Connect a ComfyUI AUDIO output.",
    "seed_audio_done": "Seed Audio generated {duration} s of audio (billed duration: {billed} s).",
    "speech_bad_url": "{field} must be an http(s):// or asset:// URL.",
    "seed_audio_prompt_empty": "text_prompt is empty.",
    "seed_audio_prompt_too_long": "text_prompt has {count} characters; the maximum is {max}.",
    "seed_audio_slot_conflict": "Reference slot {slot}: connect ref_audio_{slot} or fill ref_audio_{slot}_source, not both.",
    "seed_audio_slot_gap": "Fill the reference audio slots in order: slot {slot} is used but slot {missing} is empty. Slot N is @AudioN in the prompt.",
    "seed_audio_image_and_audio": "An image reference cannot be combined with audio references.",
    "seed_audio_image_conflict": "Use ref_image or ref_image_url, not both.",
    "seed_audio_ref_too_long": "Reference audio {slot} is {duration} s long; the maximum is {max} s.",
    "seed_audio_ref_too_large": "Reference {kind} is {size_mb} MB; the maximum is {max_mb} MB.",
    "tts_text_empty": "text is empty.",
    "tts_voice_required": "{model} needs custom_speaker_id: the voice list only holds TTS 2.0 voices.",
    "tts_tone_fidelity_icl2_only": "tone_fidelity only works with seed-icl-2.0 cloned voices.",
    "asr_lid_standard_only": "enable_lid needs seed-asr-2.0 or seed-asr-1.0 (not seed-asr-fast).",
    "asr_end_window_out_of_range": "end_window_size must be 0 (off) or between 300 and 5000 ms.",
    "asr_channel_split_needs_stereo": "enable_channel_split needs stereo audio (2 channels).",
    "tts_sample_rate_unsupported": "seed-tts-2.0 supports the sample rates 24000, 16000 and 8000 Hz.",
    "asr_no_input": "Connect audio or fill audio_url.",
    "asr_both_inputs": "Use audio or audio_url, not both.",
    "asr_audio_too_large": "The audio is {size_mb} MB as 16 kHz mono WAV; seed-asr-fast accepts up to {max_mb} MB. Use a public audio_url with seed-asr-2.0 instead.",
    "voice_clone_speaker_empty": "speaker_id is empty: enter a voice slot ID (S_...) from the Seed Speech console, or your own postpaid custom voice ID.",
    "voice_clone_custom_id_invalid": "'{value}' is not a valid custom voice ID: 8-256 letters, digits, - or _, starting with a letter, not ending with - or _, and without reserved prefixes (S_, ICL_, MIX_, xx_) or suffixes (_bigtts, _tob, _streaming).",
    "voice_clone_audio_too_large": "The reference audio is {size_mb} MB; voice cloning accepts up to {max_mb} MB. Trim it to 10-15 s.",
    "voice_clone_demo_text_length": "demo_text must be 4 to 80 characters.",
    "voice_clone_failed": "Voice cloning failed for {speaker}: {message}",
    "voice_clone_not_found": "Voice {speaker} was not found. Check the speaker ID.",
    "voice_clone_timeout": "Voice {speaker} was still training after {seconds} s. Run the node again later to check it.",
    "voice_clone_started": "Training voice {speaker}...",
    "voice_clone_ready": "Voice {speaker} is ready (status {status}). The first TTS call with it activates the voice slot and starts its billing.",
    "voice_clone_no_demo": "No demo audio was returned for {speaker}; outputting silence.",
    "speech_upload_unavailable": "This Seed Speech option needs a URL, so the connected {kind} is uploaded through Comfy.org storage, which is unavailable in this ComfyUI ({e}). Pass a public URL instead.",
    "speech_upload_failed": "Uploading the {kind} to Comfy.org storage failed: {e}. Log in to your Comfy.org account in ComfyUI (or set a Comfy.org API key), or pass a public URL instead.",
    "speech_upload_done": "Uploaded the {kind} to Comfy.org storage.",
    "asr_standard_needs_url": "{model} takes audio as a URL: connected audio is uploaded to Comfy.org storage first.",
    "asr_language_standard_only": "{language} is only supported by seed-asr-2.0 and seed-asr-1.0.",
    "asr_format_unknown": "Set audio_format: the audio_url has no recognizable extension and {model} requires the format.",
    "asr_format_fast_unsupported": "seed-asr-fast accepts raw, wav, mp3 and ogg audio; use seed-asr-2.0 for {format}.",
    "asr_context_language": "Hotwords and context only work with the Chinese-English model: set language to auto or zh-CN and turn off enable_auto_lang.",
    "asr_context_image_2_only": "context_image needs seed-asr-2.0 or seed-asr-fast (visual context is an ASR 2.0 feature).",
    "asr_wait_timeout": "The ASR task {task_id} had no result after {seconds} s.",
    "tts_context_text_2_only": "context_text only works with seed-tts-2.0.",
    "speech_stream_unreadable": "Seed Speech returned a stream that could not be read (at character {position}).",
    "speech_unexpected_response": "Seed Speech {operation} returned an unexpected response: {body}",
    "asr_context_image_conflict": "Use context_image or context_image_url, not both.",
    "asr_silent_audio": "No speech was found in the audio.",
    "asr_task_submitted": "ASR task submitted: {task_id}",

    "api_errors": {
        "AuthenticationError": "Invalid API Key (401). Check the key in api_keys.json, and that the API Client region matches the region the key was created in.",
        "AccessDenied": "Access Denied (403). No permission or IP whitelist issue.",
        "AccountOverdueError": "Account Overdue (403). Top up your BytePlus account in the BytePlus console billing center.",
        "ServiceOverdue": "Service Overdue (403). Top up your BytePlus account in the BytePlus console billing center.",
        "ServiceNotOpen": "Service Not Open (403). Activate the model in the ModelArk console (Model activation).",
        "ModelNotOpen": "Your account %s has not activated the model %s. Activate it in the ModelArk console (Model activation) for the region selected in the API Client.",
        "TaskRunningCannotCancel": "Task is currently running and cannot be cancelled (409).",
        "RateLimitExceeded": "Rate Limit Exceeded (429). Please try again later.",
        "RateLimitExceeded.EndpointRPMExceeded": "Endpoint RPM limit exceeded (429). Please try again later.",
        "RateLimitExceeded.EndpointTPMExceeded": "Endpoint TPM limit exceeded (429). Please try again later.",
        "QuotaExceeded": "Account %s has exhausted the free trial quota for model %s (429). Activate the model service in the ModelArk console.",
        "SetLimitExceeded": "Account %s has reached the set inference limit for model %s (429). Adjust the limit or disable Safe Experience Mode on the ModelArk Model activation page.",
        "ServerOverloaded": "Server Overloaded (429). Please try again later.",
        "InternalServiceError": "Internal Service Error (500). Please try again later.",
        "SensitiveContentDetected": "Sensitive Content Detected (400). Please change your prompt.",
        "InputTextSensitiveContentDetected": "Input text contains sensitive content (400).",
        "InputImageSensitiveContentDetected": "Input image contains sensitive content (400).",
        "InputVideoSensitiveContentDetected": "Input video contains sensitive content (400).",
        "InputAudioSensitiveContentDetected": "Input audio contains sensitive content (400).",
        "OutputTextSensitiveContentDetected": "Generated text contains sensitive content (400).",
        "OutputImageSensitiveContentDetected": "Generated image contains sensitive content (400).",
        "OutputVideoSensitiveContentDetected": "Generated video contains sensitive content (400).",
        "OutputVideoSensitiveContentDetected.PolicyViolation": "Generated video may be restricted due to copyright policy (400).",
        "OutputAudioSensitiveContentDetected": "Generated audio contains sensitive content (400).",
        "InputMediaMayContainRealPerson": "Request failed: input image or video may contain a real person.",
        "InvalidImageURL": "Invalid Image URL (400).",
        "InvalidImageDetail": "Invalid Image Detail parameter (400).",
        "MissingParameter": "Missing Parameter (400).",
        "InvalidParameter": "Invalid Parameter (400).",
        "InvalidParameter.TaskTypeConstraint": "The task type is incompatible with the current parameters. Video editing requires the adaptive aspect ratio and auto duration; video extension requires the adaptive aspect ratio.",
    "InvalidParameter.TaskTypeMismatch": "The model detected a different task type than the one selected in task_type. Adjust the prompt or set task_type to auto.",
        "RequestBodyTooLarge": "Request body exceeds the service limit. Reduce reference media and try again.",
        "ModelParameterNotSupported": "One or more request parameters are not supported by the selected model.",
        "MediaInvalid": "Reference media violates a format, codec, size, duration, or frame-rate limit.",
        "InvalidResolutionParameter": "Invalid resolution for current model (400). Please choose a supported resolution.",
        "LastFrameNotSupported": "Last frame input is not supported by this model. Remove it or choose a model that supports first and last frames.",
        "RefImageNotSupported": "Reference image input is not supported by this model.",
        "PromptEmpty": "Prompt cannot be empty (400).",
    }
}

ERROR_TEXT_MATCH_RULES = {
    "output image may contain sensitive information": "OutputImageSensitiveContentDetected",
    "input text may contain sensitive information": "InputTextSensitiveContentDetected",
    "input image may contain sensitive information": "InputImageSensitiveContentDetected", 
    "the request failed because the input video may contain real person": "InputMediaMayContainRealPerson",
    "the request failed because the input image may contain real person": "InputMediaMayContainRealPerson",
    "input video may contain sensitive information": "InputVideoSensitiveContentDetected",
    "input audio may contain sensitive information": "InputAudioSensitiveContentDetected",
    "output video may contain sensitive information": "OutputVideoSensitiveContentDetected",
    "output audio may contain sensitive information": "OutputAudioSensitiveContentDetected",
    "output video may be related to copyright restrictions": "OutputVideoSensitiveContentDetected.PolicyViolation",
    "policy violation": "OutputVideoSensitiveContentDetected.PolicyViolation",
    "requests per minute \\(rpm\\) limit of the associated endpoint": "RateLimitExceeded.EndpointRPMExceeded",
    "tokens per minute \\(tpm\\) limit of the associated endpoint": "RateLimitExceeded.EndpointTPMExceeded",
    "has reached the set inference limit": "SetLimitExceeded",
    "safe experience mode": "SetLimitExceeded",
    "generated text contains sensitive content": "OutputTextSensitiveContentDetected",
    "API key or AK/SK in the request is missing or invalid": "AuthenticationError",
    "account has an overdue balance": "AccountOverdueError",
    "has not activated the model": "ModelNotOpen",
    "please activate the model service in the ark console": "ModelNotOpen",
    "exceeded the quota": "QuotaExceeded",
    "limit of the associated endpoint": "RateLimitExceeded",
    "Request failed because it is missing": "MissingParameter",
    "the task is determined as video editing": "InvalidParameter.TaskTypeConstraint",
    "parameters specified in the request are not valid": "InvalidParameter",
    "parameter resolution specified in the request is not valid": "InvalidResolutionParameter",
    "not permitted to access": "AccessDenied",
    "service is unavailable": "ServiceNotOpen",
    "does not support last frame image": "LastFrameNotSupported",
    "does not support reference image": "RefImageNotSupported",
    "prompt cannot be empty": "PromptEmpty",
    "text content must contain a prompt description": "PromptEmpty",
    "because it is currently running": "TaskRunningCannotCancel",
}
