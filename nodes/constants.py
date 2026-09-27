# BytePlus ModelArk constants and console messages

# Data-plane endpoints per region. The first entry is the default.
REGION_BASE_URLS = {
    "ap-southeast-1": "https://ark.ap-southeast.bytepluses.com/api/v3",
    "eu-west-1": "https://ark.eu-west.bytepluses.com/api/v3",
}
DEFAULT_REGION = "ap-southeast-1"

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
    "init_dep_check_err": "Dependency check error; the BytePlus nodes are disabled: {e}",
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
    "err_seedance2_resolution_unsupported": "Model {model} does not support {resolution}. Supported resolutions: {supported}.",
    "err_seedance2_duration_unsupported": "Model {model} requires a duration between {min} and {max} seconds. Current: {duration}",
    "err_seedance25_editing_params": "Seedance 2.5 video editing (task_type edit) requires the adaptive aspect ratio and auto duration.",
    "err_no_draft_to_reuse": "reuse_last_draft_task is on, but this node has no draft to reuse (drafts are remembered per node and model until ComfyUI restarts). Turn off reuse and paste the draft task ID into draft_task_id, or generate a draft first.",
    "err_draft_final_resolution": "Final videos rendered from a {model} draft support only {supported}. Current resolution: {resolution}.",
    "err_seedance25_first_frame_ratio": "Seedance 2.5 image-to-video keeps the first frame's aspect ratio. Set aspect_ratio to adaptive.",
    "err_seedance25_task_type_needs_video": "Seedance 2.5 task_type '{task_type}' needs at least one reference video.",
    "err_seedance25_extend_params": "Seedance 2.5 video extension requires the adaptive aspect ratio.",
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
