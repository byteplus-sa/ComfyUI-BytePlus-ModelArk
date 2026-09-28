---
name: byteplus-node-maintenance
description: How this BytePlus ModelArk node pack works end-to-end and how to change it safely — adding or updating Seedream/Seedance/Seed models and dated model IDs, adding node parameters, the video submit/poll/cancel executor, image streaming, visual Responses API path, API keys/regions, quota, Comfy.org reference-video upload, error messages, and how it compares with ComfyUI core's own ByteDance partner nodes. Use for any feature work, model update, bug fix or review in nodes/*.py.
---

# BytePlus ModelArk pack — maintenance guide

Read `CLAUDE.md` first (layout, rules, checklists). This skill adds the mechanics. For ComfyUI API details use the `comfyui-v3-nodes` and `comfyui-frontend-extensions` skills; for tests/versioning/publishing use `comfyui-release-and-testing`.

## Request lifecycle

**Client.** `BytePlusAPIClient.execute` (`nodes_shared.py`) picks a key (`api_keys.json` entry, or `Custom` + `new_api_key`, validated by a GET on the base URL) and a region from `constants.REGION_BASE_URLS` (`ap-southeast-1` default, `eu-west-1`), builds `Ark(api_key=…, base_url=…)` from `byteplussdkarkruntime`, and returns `BytePlusClients` on the custom socket type `BYTEPLUS_CLIENT`. Every other node takes that socket. The only env vars read are `BYTEPLUS_ACCESS_KEY` / `BYTEPLUS_SECRET_KEY` / `BYTEPLUS_SESSION_TOKEN`, as a fallback for asset-library credentials when the `api_keys.json` entry has no `accessKey`/`secretKey`.

**Asset library (`nodes_assets.py`).** Signed ModelArk OpenAPI via `byteplussdkcore.universal.UniversalApi` (IAM AK/SK, host `ark.<region>.byteplusapi.com`, returns `Result`; errors are `ApiException` with a `ResponseMetadata.Error` JSON body, formatted by `_format_asset_error`). Actions: `ListAssetGroups`, `CreateAssetGroup` (GroupType `AIGC`), `CreateAsset` (needs an HTTPS URL → local images go through `upload_image_to_comfyapi`), `GetAsset` (`Processing` → `Active` | `Failed`), `ListAssets` (Filter.GroupType required: `AIGC` or `LivenessFace`). Seedance uses Active assets as `asset://<id>` in `ref_image_urls` / `ref_video_urls` / `ref_audio_urls` — that part needs only the API key.

**Video (Seedance).** Node `execute` → `BytePlusVideoBase._common_generation_logic` (`nodes_video.py`):
1. `_raise_if_text_params(prompt, forbidden_params)` — rejects `--resolution/--ratio/--dur/...` flags in the prompt (parameters are sent as JSON fields, not prompt flags).
2. Builds `extra_api_params` (`resolution`, `ratio`, `seed` (-1 when random), `duration` or `frames`, `duration=-1` for auto), estimates tokens, `client.check_quota(...)`.
3. Prepends `{"type":"text","text":prompt}` to `content` (images/videos/audio items with `role`s built by the node).
4. `BytePlusGenerationExecutor.run_batch_tasks` (`executor.py`): `generation_count` parallel `asyncio.to_thread(ark.content_generation.tasks.create, …)`; Seedance 2.x (`"seedance-2-"` in the model ID) strips `service_tier`/`execution_expires_after` and enforces the 64 MiB request limit; estimates duration from task history; polls `tasks.get` every 2 s; reports progress; on interrupt **deletes pending tasks** (`tasks.delete`) and re-raises.
5. Downloads via `utils_download` into temp `BytePlus/`, returns `(VideoFromFile, last_frame, json)`.
Modes: `non_blocking` (task IDs cached per node id; re-run to collect), draft mode (1.5: `LAST_SEEDANCE_1_5_DRAFT_TASK_ID`; 2.5: `LAST_SEEDANCE_2_DRAFT_TASKS` + `_render_final_from_drafts` sending `{"type":"draft_task","draft_task":{"id":…}}`), `enable_offline_inference` → `service_tier="flex"` (1.0/1.5 only). If several nodes of the same class are in one prompt, failures are logged and a white placeholder is returned instead of raising.

**Image (Seedream).** `BytePlusGenerationExecutor.run_parallel_requests`; Seedream 4 / 5 Lite stream (`stream_generation_helper`, SDK `images.generate(stream=True)` in a producer thread, b64 results); 5 Pro uses `response_format="url"` + download; transparent background via `extra_body={"background": "transparent"}`; Layers uses `layer_decomposition=True`.

**Visual (Seed).** `BytePlusVisualExecutor` → Responses API (`ark.responses.create`), files uploaded via Ark Files API (`upload_file_to_ark`, cached in `files_upload_cache.json` by sha256), multi-turn via `LAST_RESPONSES[node_id]` (`previous_response_id`, reused only with the same API key). `wait_for_file_active` polls the Files API (`processing | active | failed`), giving up after 10 min or 5 consecutive errors and honouring interrupts.

**Reference videos (Seedance 2.x).** Local `ref_videos` → `upload_video_to_comfy_storage` → lazily imported `comfy_api_nodes.util.upload_video_to_comfyapi(cls, video, wait_label=None)` (needs Comfy.org login/API key via `Hidden.auth_token_comfy_org` / `api_key_comfy_org` on the schema; unavailable with `--disable-api-nodes`); cached 12 h in `COMFY_VIDEO_UPLOAD_CACHE`. `ref_video_urls` (public URLs or `asset://…`) bypasses it. `comfy_api_nodes` is **not a stable API** — keep the import lazy and wrapped, and re-check the signature when raising the ComfyUI floor.

## Adding or updating a model

1. **New dated version of an existing model** → change only the value in `nodes/models_config.py` (UI name stays; saved workflows keep working).
2. **New model** → add to the right map and option list in `models_config.py`:
   - Seedance 2-family: `VIDEO_MODEL_MAP`, `VIDEO_2_UI_OPTIONS`, `VIDEO_2_MODEL_RESOLUTIONS`, `VIDEO_2_MODEL_MAX_DURATIONS`, `VIDEO_2_MODEL_REFERENCE_LIMITS`; if it has 2.5 features also `SEEDANCE_2_5_FAMILY` and `SEEDANCE_DRAFT_FINAL_RESOLUTIONS` (missing → KeyError while building the schema → **whole pack fails to load**).
   - Seedream: `SEEDREAM_4_MODEL_MAP` / `SEEDREAM_5_MODEL_MAP`; behaviour branches key on `SEEDREAM_4_0_UI_MODEL` / `SEEDREAM_5_PRO_UI_MODEL`; size presets in `nodes_image_schema.py`; pixel limits in `constants.py`; JS hard-codes `"seedream-4-0"` for `prompt_optimization` visibility.
   - Visual: `VISUAL_MODEL_MAP` only (first entry = default).
3. Check substring-driven logic: `"seedance-2-"` in `executor.py` (request policy + time estimate).
4. Add validation for new limits next to the existing `validate_seedance2_*` helpers in `nodes_video.py`.
5. Update README model list, `tests/test_model_updates.py`, and — if it becomes the default — example workflows + expected orders in `tests/test_workflow_templates.py`.
6. Confirm the model ID and parameters against BytePlus docs (use the `arkcli-docs` / `arkcli-models` skills) — core ComfyUI's IDs can differ (see [core-partner-nodes.md](core-partner-nodes.md)).

## Adding a parameter

`define_schema` input (or `_model_inputs(...)` for DynamicCombo nodes: Seedream 5, Seedance 2) → `execute` kwarg (DynamicCombo: unpack `model_config.get("<name>", default)` from the `model_version` dict) → map into request kwargs / `extra_api_params` → add prompt-flag name to `forbidden_params` if the API also accepts it as `--flag` → messages in `constants.MESSAGES` → JS `TARGET_WIDGETS`/`widgetLogic` if it drives visibility → example workflow `inputs` + `widgets_values` positions → `tests/test_workflow_templates.py` expected orders. Prefer `advanced=True` for rarely-changed widgets.

## Conventions

- User text: add keys to `constants.MESSAGES`; use `get_text(key, **kw)` / `log_msg(key, **kw)`. Missing keys yield `""` silently — grep that every key you use exists.
- Errors: `raise BytePlusException(get_text(...))`; convert SDK/HTTP errors with `format_api_error(e)`; extend `ERROR_TEXT_MATCH_RULES` for new provider error codes (core maps e.g. `OutputAudioSensitiveContentDetected.PolicyViolation`, `InvalidParameter.TaskTypeConstraint`, `InvalidParameter.TaskTypeMismatch`).
- Interrupts: every wait loop calls `comfy.model_management.throw_exception_if_processing_interrupted()` at least every ~1–2 s; catch `InterruptProcessingException` only to clean up (cancel remote tasks), then re-raise.
- Sync SDK calls go through `asyncio.to_thread`; HTTP downloads use `aiohttp`.
- Per-node state: module-level or class-level **dicts mutated in place**, keyed by `cls.hidden.unique_id` (reassigning a class attribute inside `execute` raises — the class is locked).
- Never log or persist API keys; `api_keys.json` and `files_upload_cache.json` are git-ignored runtime files.

## Known weak spots (check before building on them)

- Video polling has no client-side limit (relies on ModelArk task expiry: `execution_expires_after` 48 h for 1.x, server default for 2.x) — interruptible, so acceptable.
- `API Client` in `Custom` mode puts the raw key in the prompt (workflow + output metadata) until the first run saves it under `new_key_name`; `_notify_api_key_saved` then sends `byteplus.api_key_saved` and the JS clears the key. Without `new_key_name` the key stays.
- Local video work uses PyAV (`probe_video_file`, `extract_last_frame_tensor`) and ComfyUI's `InputImpl.VideoFromComponents` (`create_white_video`) — don't reintroduce OpenCV; ComfyUI doesn't ship it.
- No API-call retries (only downloads retry). See [core-partner-nodes.md](core-partner-nodes.md) for core's retry policy if adding them.
