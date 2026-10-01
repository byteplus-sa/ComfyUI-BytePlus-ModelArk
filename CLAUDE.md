# ComfyUI-BytePlus-ModelArk

ComfyUI custom node pack that calls **BytePlus ModelArk** directly with the user's own ModelArk API key: Seedream (image), Seedance (video), Seed (visual understanding). It also calls **BytePlus Seed Speech** (Seed Audio 1.0, TTS, ASR) with a separate Seed Speech API key, and **BytePlus VOD AI MediaKit** (vCube Video Enhance, Video Smoothness Enhance, Image Quality Enhance) with a separate MediaKit API key. Built on the ComfyUI **V3 node API** (`comfy_api.latest`). The nodes mirror ComfyUI core's ByteDance partner nodes (see Project rules). Requires ComfyUI ≥ 0.31.0; supports Classic Canvas and Nodes 2.0 (Vue nodes).

`AGENTS.md` is a symlink to this file — edit `CLAUDE.md` only.

The project release skill is `.agents/skills/comfyui-registry-release/SKILL.md`. Use it for end-to-end Comfy Registry releases, including packaging, publication, and version status checks.

## Project skills (`.claude/skills/`)

| Skill | Use for |
|---|---|
| `byteplus-node-maintenance` | Request lifecycle, adding models/parameters, conventions, comparison with ComfyUI core's ByteDance nodes |
| `comfyui-v3-nodes` | ComfyUI V3 backend API: schema, inputs, DynamicCombo/Autogrow value shapes, hidden inputs, async/progress/interrupts |
| `comfyui-frontend-extensions` | `web/js` work: hooks, widget visibility in Classic Canvas and Nodes 2.0, websocket events, deprecations |
| `comfyui-release-and-testing` | Running node tests, example-workflow sync (template checklist), and ComfyUI smoke tests |
| `byteplus-model-update` | Adding, re-dating, retiring or region-limiting a model: every table to touch and the finish checklist |
| `byteplus-core-parity-sync` | Catching up with changes to ComfyUI core's ByteDance nodes: diff, parity tests, follow vs. deviate |
| `byteplus-new-service-node` | Nodes on Seed Speech, AI MediaKit or another product with its own key: key store, client, transport, tests, Speech quirks |
| `byteplus-live-api-probe` | Checking real API behaviour safely (cost, keys, cleanup) and recording what was verified |

## Layout

| Path | What lives there |
|---|---|
| `__init__.py` | Dependency check (`check_dependencies()`: if the Ark SDK is missing/too old, prints the install command and registers no nodes — never pip-installs at runtime, a Comfy Registry rule), global traceback/logging patches, `BytePlusExtension(ComfyExtension)` + `comfy_entrypoint()`, `WEB_DIRECTORY = "./web"`. New nodes must be added to `_registered_nodes`. |
| `nodes/models_config.py` | **Model catalog.** UI name → dated model ID maps (`SEEDREAM_4/5_MODEL_MAP`, `VIDEO_MODEL_MAP`, `VISUAL_MODEL_MAP`, …), per-node option lists, Seedance 2 capability tables. |
| `nodes/constants.py` | Region base URLs, pixel/ratio/media limits, frame math, **all user-facing text** (`MESSAGES`), `ERROR_TEXT_MATCH_RULES`. |
| `nodes/nodes_shared.py` | `BytePlusAPIClient` node, `ApiKeyStore` (`api_keys.json`), `GLOBAL_CATEGORY`, `BytePlusException`, `format_api_error`, Ark Files API upload (`upload_file_to_ark`, `wait_for_file_active`), PyAV video helpers (`probe_video_file`, `extract_last_frame_tensor`, `create_white_video`; no OpenCV), interrupt-aware `wait_interruptible` / `sleep_interruptible`, and `upload_bytes_to_comfy_storage` (cached Comfy.org upload for APIs that only take links; used by Seed Speech and MediaKit). |
| `nodes/executor.py` | `BytePlusGenerationExecutor` (video submit → poll → cancel-on-interrupt; image parallel/stream), `BytePlusVisualExecutor` (Responses API), progress reporting. |
| `nodes/core_style.py` | Shared pieces for the nodes shaped like ComfyUI core's ByteDance nodes: core's `seed_input` / `watermark_input`, this pack's extras as advanced inputs (`video_extra_inputs`), `raise_if_output_linked` (core's `validate_output_unlinked`, reads `Hidden.prompt`), and reference resolution for `asset_N` / `*_asset_id` (`resolve_reference_values`, `resolve_typed_reference`: asset ID, `asset://` or https link; asset types via `GetAsset`, which needs AK/SK). |
| `nodes/nodes_seedream.py` | Core-style `BytePlusSeedream` (Seedream 4.5 & 5.0, `model` DynamicCombo) and `BytePlusSeedreamLayerSeparation`. |
| `nodes/nodes_seedance1.py` | Core-style Seedance 1.x: `BytePlusSeedanceTextToVideo`, `…ImageToVideo`, `…FirstLastFrame`. |
| `nodes/nodes_seedance2.py` | Core-style Seedance 2 / 2.5: `BytePlusSeedance2TextToVideo`, `…FirstLastFrame`, `…Reference` (Autogrow references inside each model option, `asset_N`, `assetN` prompt rewrite), `BytePlusSeedanceDraftToFinal` (reads the model from the draft task; 2.5 and 2.5 Premium drafts). |
| `nodes/nodes_seed.py` | Core-style `BytePlusSeed` (displayed as "BytePlus LLM"; the node ID stays `BytePlusSeed`; Responses API, Files API media). Besides the Seed 2.x models its `model` combo lists DeepSeek V4.1 Flash and GLM 5.3 Flash: same request, same inputs (they take images and video and the same thinking/effort values). Modalities (checked live): all six models take text, images (20) and videos (4); only Seed 2.0 Lite and Mini take audio (`SEED_LLM_AUDIO_MODELS` → an extra `audios` Autogrow, WAV via Files API as `input_audio`, 4 clips / 120 min). Seed 2.0 Pro and 2.1 Turbo reject audio, DeepSeek V4.1 Flash and GLM 5.3 Flash accept it but cannot hear it, so they get no audio input. `reasoning_effort` offers minimal/low/medium/high/max; the API accepts `max` on every listed model (checked live: it lengthens reasoning on Seed 2.0 Pro/Lite/2.1 Turbo and GLM 5.3 Flash), so it is not gated per model. |
| `nodes/nodes_image.py` | Legacy: Seedream 4, Seedream 5, Seedream Layer Decomposition; shared Seedream request helpers. |
| `nodes/nodes_video.py` | Legacy: Seedance 1.0 / 1.5 / 2 & 2.5. Also Video Query Tasks, dev-only Progress Test, `BytePlusVideoBase` helpers, Seedance validation helpers and Comfy.org reference-video upload used by the new nodes. |
| `nodes/nodes_visual.py` | Legacy: Visual Understanding. |
| `nodes/nodes_speech.py` | Seed Speech nodes: `BytePlusSpeechClient` (keys in `speech_api_keys.json` or `BYTEPLUS_SEED_SPEECH_API_KEY`), `BytePlusSeedAudio` (`/api/v3/tts/create`, one request), `BytePlusSeedTTS` (`/api/v3/tts/unidirectional`, streamed JSON chunks of raw PCM; model = `X-Api-Resource-Id`), `BytePlusSeedASR` (fast `recognize/flash`, or standard `submit` + `query` polled on the `X-Api-Status-Code` header), `BytePlusSeedVoiceClone` (`/api/v3/tts/voice_clone` + `get_voice` polling; outputs a speaker ID for TTS `seed-icl-2.0`). Uploaded clips/images go inline as base64 except where the API only takes URLs (standard ASR audio, ASR context image): those go through `upload_to_comfy_storage` (Comfy.org, cached 12 h). Request builders are module-level functions (tested directly). |
| `nodes/speech_api.py` | Seed Speech transport: `SeedSpeechClient` on the `BYTEPLUS_SPEECH_CLIENT` socket, `speech_post` (aiohttp, `X-Api-Key`, `X-Api-Request-Id`, interrupt-aware), error-code mapping (`describe_speech_error`), stream parser. Tests replace `_send`. |
| `nodes/nodes_mediakit.py` | AI MediaKit: `BytePlusMediaKitClient` (keys in `mediakit_api_keys.json` or `BYTEPLUS_VOD_MEDIAKIT_API_KEY`; `BYTEPLUS_MEDIAKIT_CLIENT` socket), `BytePlusVideoEnhance` (core's vCube node, `POST /tools/enhance-video`; `build_enhance_request` follows core's parameter mapping), `BytePlusVideoSmoothness` (not in core; `POST /tools/enhance-video-smoothness`) and `BytePlusImageEnhance` (not in core; synchronous `POST /tools-sync/enhance-image`, 600 s timeout; per-version limits in `IMAGE_VERSION_LIMITS`, checked by `plan_image_enhance` before upload). Video tools: submit → poll `GET /tasks/{id}` → download within the 24 h link life (`submit_and_wait`; `Authorization: Bearer`). Requests and downloads are interruptible (`wait_interruptible`). Downloads have no total time limit, only a stall limit (results can be gigabytes), and their errors never show the signed query string. Polling retries only network errors, timeouts, 429 and 5xx (`MediaKitRequestError.retryable`); a task with status `failed` is reported at once. Local videos go through Comfy.org storage (`upload_source`); local images too, as PNG (JPEG over 10 MB), cached per content (`upload_image_source`). Comparisons are built locally with PyAV on the result's timeline (`_render_synced`): vCube's sweeping-divider video plus a `source_frame`/`enhanced_frame` pair for core's `ImageCompare`; Smoothness's side-by-side video. `make_comparison` builds them on a temporary source copy (deleted afterwards); a failed comparison is logged and skipped, so the paid result is still returned. Outputs that have nothing to show return `ExecutionBlocker(None)`, so nodes using them are skipped instead of failing on `None`. Smoothness passes the source through when MediaKit returns no `video_url` (nothing repaired). Tests replace `_send`. |
| `nodes/audio_utils.py` | AUDIO ⇄ WAV/PCM, PyAV decoding, subtitle normalization and SRT. |
| `nodes/seed_speech_voices.py` | TTS 2.0 voice list generated from the official voice list page. Speaker IDs are saved in workflows: add, never rename. |
| `nodes/nodes_assets.py` | Private asset library (Advanced Creation Rights): core-style `BytePlusCreateImageAsset` / `…VideoAsset` / `…AudioAsset` (existing `group_id`, or find/create an AIGC group by `group_name`; media socket or https URL), Legacy `BytePlusVirtualPortraitAsset` (find/create AIGC group → Comfy.org image upload → `CreateAsset` → poll `GetAsset` until Active → `asset://` URI) and `BytePlusAssetLibrary` (`ListAssets`). Signed OpenAPI via the SDK's `UniversalApi` (service `ark`, version `2024-01-01`, host `ASSET_API_HOSTS[region]`) with IAM AK/SK from the `api_keys.json` entry (`accessKey`/`secretKey`/`sessionToken`) or `BYTEPLUS_ACCESS_KEY`/`BYTEPLUS_SECRET_KEY`. |
| `nodes/nodes_*_schema.py` | Input-builder helpers (not validators). |
| `nodes/quota.py` | In-memory per-key/per-model quota guard + `BytePlusQuotaSettings` node. |
| `nodes/utils_download.py` | aiohttp download helpers, `save_to_output`. |
| `web/js/byteplus_dynamic_widgets.js` | Widget show/hide rules (`TARGET_WIDGETS`, `widgetLogic`, triggered by chained `widget.callback`), Autogrow labels, DynamicCombo value-restore workaround, `byteplus.api_key_saved` listener. |
| `web/js/byteplus_progress.js` | Canvas progress bar driven by the `progress` websocket event. |
| `example_workflows/*.json` | Templates shipped to the ComfyUI template browser; guarded by tests. They use only core-style nodes. Each has a `<name>.jpg` thumbnail (a screenshot of the graph); retake it when the template changes. |
| `.comfyignore` | Dev files excluded from the registry package. |

## Commands

```bash
# Template/workflow tests (no ComfyUI needed)
python3 -m unittest tests.test_workflow_templates

# Node behaviour tests (skipped unless COMFYUI_ROOT is set; the interpreter needs torch,
# ComfyUI's requirements and this pack's requirements)
COMFYUI_ROOT=/path/to/ComfyUI /path/to/python -m unittest tests.test_model_updates
```

Tests use stdlib `unittest` (pytest is not a dependency). `test_model_updates` fakes Ark clients with `SimpleNamespace` and stubs `PromptServer` / `comfy_api_nodes.util` — follow that pattern; never hit the real API in tests.

## Project rules

- **BytePlus naming everywhere.** Classes, node IDs and JS keys start with `BytePlus` (the JS matches on `comfyClass.startsWith("BytePlus")`). Never reintroduce `Jimeng`, `doubao-*` model IDs, Volcengine SDK imports or Chinese strings — tests assert this.
- **English only.** There is no `locales/` directory; user-facing text lives in `constants.MESSAGES` and in `display_name`/`tooltip` strings. Use `get_text(key, **kw)` / `log_msg(key, **kw)`; a missing key silently yields `""`, so add the key when you add a call.
- **BytePlus models only.** No models without a BytePlus ModelArk, BytePlus Seed Speech or BytePlus VOD AI MediaKit equivalent.
- **Three key types.** ModelArk keys (`api_keys.json`, `BytePlusAPIClient`, `Authorization: Bearer`), Seed Speech keys (`speech_api_keys.json`, `BytePlusSpeechClient`, `X-Api-Key`, Singapore only) and AI MediaKit keys (`mediakit_api_keys.json`, `BytePlusMediaKitClient`, `Authorization: Bearer`, Singapore only) are different products and never interchangeable; keep their sockets separate. The `byteplus.api_key_saved` event carries `store` (`modelark` / `speech` / `mediakit`) so the JS clears the right client node.
- **Core-style nodes.** New image/video/understanding/asset nodes copy ComfyUI core's ByteDance partner nodes (`comfy_api_nodes/nodes_bytedance.py`, `nodes_bytedance_llm.py`): same node split, input names/order/defaults/tooltips/`advanced` flags, DynamicCombo options and output order. Differences: `client` socket first; BytePlus model IDs (never core's); this pack's extras after core's inputs, `advanced=True` **and** `optional=True` (the frontend lists required inputs before optional ones); extra outputs after core's. Like core's, they are not output nodes (no `is_output_node`) and never write files themselves: a node whose outputs nothing uses does not run (and is not billed), and Save Video / Save Image keep results. The core-style Seedance `VIDEO` output is a list output, so every video of a `generation_count` batch reaches it (seed + index per task), with `last_frame` as one batch in the same order. Tests compare `output_node` with core's. A node that reads `cls.hidden.prompt` must list `Hidden.prompt` itself (only output nodes get it automatically). Older nodes stay registered with `is_deprecated=True` and "(Legacy)" names — never change their inputs; they stay output nodes and still save `generation_count` batches to the output folder. Tests compare each new schema with core's live schema. Where the BytePlus docs give different limits than core (reference-video pixel range, Seedream 4.x reference count, `max_images`, asset limits, layer input size), follow BytePlus and list the input in the test's deviation set; core's per-resolution video budgets are only `auto_downscale` targets.
- **Reference media:** connected images and audio go inline as base64; local videos go through Comfy.org storage (`comfy_api_nodes.util.upload_video_to_comfyapi`, needs Comfy.org login). Local media is never registered as a private asset (core's "virtual library"). `asset_N` / `*_asset_id` take asset IDs, `asset://` or https links (the bypass); on Legacy nodes `ref_video_urls` / `ref_image_urls` / `ref_audio_urls` do the same.
- **Asset library credentials** (IAM AK/SK) never go into widgets, outputs or logs; they live only on `BytePlusClients.asset_credentials`.
- **Errors:** raise `BytePlusException` with a `[BytePlus]`-prefixed message; always re-raise `InterruptProcessingException`; format API errors via `format_api_error`.
- Node IDs are part of saved workflows — renaming one breaks users' workflows.

## Change checklists

**Add/rename an input** → `define_schema` (or `_model_inputs` for DynamicCombo nodes) → `execute` kwarg (DynamicCombo values arrive as a dict under `model` on core-style nodes, `model_version` on Legacy ones; unpack them) → request mapping → `MESSAGES` → JS `TARGET_WIDGETS` if it drives visibility → example workflow `inputs` order **and** `widgets_values` positions → expected orders in `tests/test_workflow_templates.py`.

**Add a model** → `models_config.py` maps/options/capability tables (Seedance 2.5-family also needs `SEEDANCE_2_5_FAMILY` and `SEEDANCE_DRAFT_FINAL_RESOLUTIONS`, else KeyError at schema build) → check `"seedance-2-"` substring logic in `executor.py` → README model list → `tests/test_model_updates.py`.

**Retire a model** → remove it from every map/option list in `models_config.py` and add it to `RETIRED_MODELS` (UI name → (model ID, replacement)); Legacy nodes keep the name in their combo and call `core_style.raise_if_model_retired` so saved workflows load and explain the switch. Region-limited models go in `MODEL_REGION_EXCLUSIONS` (checked by `raise_if_model_unavailable_in_region`).

**Bump version** → `pyproject.toml` `version` **and** `properties.ver` on every BytePlus node in every `example_workflows/*.json` (tested) **and** README status line.

**Publish** → `.github/workflows/publish_action.yml` is manual (`workflow_dispatch`) and only runs on `main`: it tags `v{version}` and publishes to the Comfy Registry.

## Gotchas

- Module-level state keyed by node id: non-blocking task cache, draft task IDs, Comfy.org upload cache, Visual multi-turn `LAST_RESPONSES` (also keyed by API key). Mutate these dicts in place; `execute` runs on a locked class.
- Video polling has no client-side timeout (bounded by ModelArk task expiry; interruptible). `wait_for_file_active` gives up after 10 min, on status `failed`, or after 5 consecutive retrieve errors.
- `__init__.py` replaces `traceback.print_exception`, `traceback.format_exception` and `logging.error` process-wide; the wrappers only change output for exceptions marked `byteplus_suppress_traceback`.
- While the API Client is on `Custom`, the raw key is in the workflow and in output metadata. After it is saved under `new_key_name`, the backend sends `byteplus.api_key_saved` **only to the client that queued the prompt** (`PromptServer.instance.client_id`) with a SHA-256 fingerprint of the key (never the key). The JS switches every API Client — root graph and subgraphs — whose pasted key matches the fingerprint, clears it, and calls the change tracker so the cleared state is saved.
- Frontend 1.52.7 (shipped with ComfyUI 0.37) has no widget visibility API: Nodes 2.0 ignores `widget.hidden`, so the JS disables widgets there instead. Never redefine `widget.value` — it bypasses the frontend's widget value store.
- `api_keys.json`, `speech_api_keys.json`, `mediakit_api_keys.json` and `files_upload_cache.json` are runtime files in the repo root — never commit them.
- Seed Speech docs and responses disagree in places: Seed Audio subtitles may come as `sentences` or `utterances`, success codes are `0` (Seed Audio) or `20000000` (TTS/ASR), and TTS subtitle chunks are undocumented. The parsers accept all of these; confirm against the live API before tightening them.
- BytePlus LLM node: docs and the live API disagree on `reasoning.effort` and audio (checked live 2026-10-01). The docs list `low/medium/high/minimal` for Seed 2.x, but the API accepts `none`, `max` and `xhigh` on every model without an error (`max` really lengthens reasoning on Seed 2.0 Pro/Lite, Seed 2.1 Turbo and GLM 5.3 Flash), so `reasoning_effort` offers `max` for all. Audio: only Seed 2.0 Lite/Mini understand it; Seed 2.0 Pro and Seed 2.1 Turbo reject `input_audio` ("content type is not supported"), but DeepSeek V4.1 Flash and GLM 5.3 Flash **accept it and answer that they cannot hear**, so a model accepting a request is not proof it supports the modality. Probe with content only a model that perceives it can answer (a spoken word, a number in a video). Re-check when BytePlus adds models; the `audios` input is gated by `SEED_LLM_AUDIO_MODELS`.
