# ComfyUI-BytePlus-ModelArk

ComfyUI custom node pack that calls **BytePlus ModelArk** directly with the user's own ModelArk API key: Seedream (image), Seedance (video), Seed (visual understanding). It also calls **BytePlus Seed Speech** (Seed Audio 1.0, TTS, ASR) with a separate Seed Speech API key. Built on the ComfyUI **V3 node API** (`comfy_api.latest`). Requires ComfyUI ≥ 0.31.0; supports Classic Canvas and Nodes 2.0 (Vue nodes).

`AGENTS.md` is a symlink to this file — edit `CLAUDE.md` only.

The project release skill is `.agents/skills/comfyui-registry-release/SKILL.md`. Use it for end-to-end Comfy Registry releases, including packaging, publication, and version status checks.

## Project skills (`.claude/skills/`)

| Skill | Use for |
|---|---|
| `byteplus-node-maintenance` | Request lifecycle, adding models/parameters, conventions, comparison with ComfyUI core's ByteDance nodes |
| `comfyui-v3-nodes` | ComfyUI V3 backend API: schema, inputs, DynamicCombo/Autogrow value shapes, hidden inputs, async/progress/interrupts |
| `comfyui-frontend-extensions` | `web/js` work: hooks, widget visibility in Classic Canvas and Nodes 2.0, websocket events, deprecations |
| `comfyui-release-and-testing` | Running node tests, example-workflow sync, and ComfyUI smoke tests |

## Layout

| Path | What lives there |
|---|---|
| `__init__.py` | Dependency check (`check_dependencies()`: if the Ark SDK is missing/too old, prints the install command and registers no nodes — never pip-installs at runtime, a Comfy Registry rule), global traceback/logging patches, `BytePlusExtension(ComfyExtension)` + `comfy_entrypoint()`, `WEB_DIRECTORY = "./web"`. New nodes must be added to `_registered_nodes`. |
| `nodes/models_config.py` | **Model catalog.** UI name → dated model ID maps (`SEEDREAM_4/5_MODEL_MAP`, `VIDEO_MODEL_MAP`, `VISUAL_MODEL_MAP`, …), per-node option lists, Seedance 2 capability tables. |
| `nodes/constants.py` | Region base URLs, pixel/ratio/media limits, frame math, **all user-facing text** (`MESSAGES`), `ERROR_TEXT_MATCH_RULES`. |
| `nodes/nodes_shared.py` | `BytePlusAPIClient` node, `ApiKeyStore` (`api_keys.json`), `GLOBAL_CATEGORY`, `BytePlusException`, `format_api_error`, Ark Files API upload (`upload_file_to_ark`, `wait_for_file_active`), PyAV video helpers (`probe_video_file`, `extract_last_frame_tensor`, `create_white_video`; no OpenCV). |
| `nodes/executor.py` | `BytePlusGenerationExecutor` (video submit → poll → cancel-on-interrupt; image parallel/stream), `BytePlusVisualExecutor` (Responses API), progress reporting. |
| `nodes/nodes_image.py` | Seedream 4, Seedream 5 (DynamicCombo), Seedream Layer Decomposition. |
| `nodes/nodes_video.py` | Seedance 1.0 / 1.5 / 2 & 2.5 (DynamicCombo), Video Query Tasks, dev-only Progress Test; Seedance validation helpers; Comfy.org reference-video upload. |
| `nodes/nodes_visual.py` | Visual Understanding. |
| `nodes/nodes_speech.py` | Seed Speech nodes: `BytePlusSpeechClient` (keys in `speech_api_keys.json` or `BYTEPLUS_SEED_SPEECH_API_KEY`), `BytePlusSeedAudio` (`/api/v3/tts/create`, one request), `BytePlusSeedTTS` (`/api/v3/tts/unidirectional`, streamed JSON chunks of raw PCM; model = `X-Api-Resource-Id`), `BytePlusSeedASR` (fast `recognize/flash`, or standard `submit` + `query` polled on the `X-Api-Status-Code` header), `BytePlusSeedVoiceClone` (`/api/v3/tts/voice_clone` + `get_voice` polling; outputs a speaker ID for TTS `seed-icl-2.0`). Uploaded clips/images go inline as base64 except where the API only takes URLs (standard ASR audio, ASR context image): those go through `upload_to_comfy_storage` (Comfy.org, cached 12 h). Request builders are module-level functions (tested directly). |
| `nodes/speech_api.py` | Seed Speech transport: `SeedSpeechClient` on the `BYTEPLUS_SPEECH_CLIENT` socket, `speech_post` (aiohttp, `X-Api-Key`, `X-Api-Request-Id`, interrupt-aware), error-code mapping (`describe_speech_error`), stream parser. Tests replace `_send`. |
| `nodes/audio_utils.py` | AUDIO ⇄ WAV/PCM, PyAV decoding, subtitle normalization and SRT. |
| `nodes/seed_speech_voices.py` | TTS 2.0 voice list generated from the official voice list page. Speaker IDs are saved in workflows: add, never rename. |
| `nodes/nodes_assets.py` | Private asset library (Advanced Creation Rights): `BytePlusVirtualPortraitAsset` (find/create AIGC group → Comfy.org image upload → `CreateAsset` → poll `GetAsset` until Active → `asset://` URI) and `BytePlusAssetLibrary` (`ListAssets`). Signed OpenAPI via the SDK's `UniversalApi` (service `ark`, version `2024-01-01`, host `ASSET_API_HOSTS[region]`) with IAM AK/SK from the `api_keys.json` entry (`accessKey`/`secretKey`/`sessionToken`) or `BYTEPLUS_ACCESS_KEY`/`BYTEPLUS_SECRET_KEY`. |
| `nodes/nodes_*_schema.py` | Input-builder helpers (not validators). |
| `nodes/quota.py` | In-memory per-key/per-model quota guard + `BytePlusQuotaSettings` node. |
| `nodes/utils_download.py` | aiohttp download helpers, `save_to_output`. |
| `web/js/byteplus_dynamic_widgets.js` | Widget show/hide rules (`TARGET_WIDGETS`, `widgetLogic`, triggered by chained `widget.callback`), Autogrow labels, DynamicCombo value-restore workaround, `byteplus.api_key_saved` listener. |
| `web/js/byteplus_progress.js` | Canvas progress bar driven by the `progress` websocket event. |
| `example_workflows/*.json` | Templates shipped to the ComfyUI template browser; guarded by tests. Thumbnails (`<name>.jpg`) were removed as pre-retarget and still need regenerating. |
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
- **BytePlus models only.** No models without a BytePlus ModelArk or BytePlus Seed Speech equivalent.
- **Two key types.** ModelArk keys (`api_keys.json`, `BytePlusAPIClient`, `Authorization: Bearer`) and Seed Speech keys (`speech_api_keys.json`, `BytePlusSpeechClient`, `X-Api-Key`, Singapore only) are different products and never interchangeable; keep their sockets separate. The `byteplus.api_key_saved` event carries `store` (`modelark` / `speech`) so the JS clears the right client node.
- **Reference videos:** local videos go through Comfy.org storage (`comfy_api_nodes.util.upload_video_to_comfyapi`, needs Comfy.org login); `ref_video_urls` (public URLs or `asset://`) is the bypass. `ref_image_urls` / `ref_audio_urls` take links or `asset://` IDs the same way (Virtual Portraits).
- **Asset library credentials** (IAM AK/SK) never go into widgets, outputs or logs; they live only on `BytePlusClients.asset_credentials`.
- **Errors:** raise `BytePlusException` with a `[BytePlus]`-prefixed message; always re-raise `InterruptProcessingException`; format API errors via `format_api_error`.
- Node IDs are part of saved workflows — renaming one breaks users' workflows.

## Change checklists

**Add/rename an input** → `define_schema` (or `_model_inputs` for DynamicCombo nodes) → `execute` kwarg (DynamicCombo values arrive as a dict under `model_version`; unpack them) → request mapping → `MESSAGES` → JS `TARGET_WIDGETS` if it drives visibility → example workflow `inputs` order **and** `widgets_values` positions → expected orders in `tests/test_workflow_templates.py`.

**Add a model** → `models_config.py` maps/options/capability tables (Seedance 2.5-family also needs `SEEDANCE_2_5_FAMILY` and `SEEDANCE_DRAFT_FINAL_RESOLUTIONS`, else KeyError at schema build) → check `"seedance-2-"` substring logic in `executor.py` → README model list → `tests/test_model_updates.py`.

**Bump version** → `pyproject.toml` `version` **and** `properties.ver` on every BytePlus node in every `example_workflows/*.json` (tested) **and** README status line.

**Publish** → `.github/workflows/publish_action.yml` is manual (`workflow_dispatch`) and only runs on `main`: it tags `v{version}` and publishes to the Comfy Registry.

## Gotchas

- Module-level state keyed by node id: non-blocking task cache, draft task IDs, Comfy.org upload cache, Visual multi-turn `LAST_RESPONSES` (also keyed by API key). Mutate these dicts in place; `execute` runs on a locked class.
- Video polling has no client-side timeout (bounded by ModelArk task expiry; interruptible). `wait_for_file_active` gives up after 10 min, on status `failed`, or after 5 consecutive retrieve errors.
- `__init__.py` replaces `traceback.print_exception`, `traceback.format_exception` and `logging.error` process-wide; the wrappers only change output for exceptions marked `byteplus_suppress_traceback`.
- While the API Client is on `Custom`, the raw key is in the workflow and in output metadata. After it is saved under `new_key_name`, the backend sends `byteplus.api_key_saved` **only to the client that queued the prompt** (`PromptServer.instance.client_id`) with a SHA-256 fingerprint of the key (never the key). The JS switches every API Client — root graph and subgraphs — whose pasted key matches the fingerprint, clears it, and calls the change tracker so the cleared state is saved.
- Frontend 1.52.7 (shipped with ComfyUI 0.37) has no widget visibility API: Nodes 2.0 ignores `widget.hidden`, so the JS disables widgets there instead. Never redefine `widget.value` — it bypasses the frontend's widget value store.
- `api_keys.json`, `speech_api_keys.json` and `files_upload_cache.json` are runtime files in the repo root — never commit them.
- Seed Speech docs and responses disagree in places: Seed Audio subtitles may come as `sentences` or `utterances`, success codes are `0` (Seed Audio) or `20000000` (TTS/ASR), and TTS subtitle chunks are undocumented. The parsers accept all of these; confirm against the live API before tightening them.
