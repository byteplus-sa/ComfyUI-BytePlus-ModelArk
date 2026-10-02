---
name: byteplus-new-service-node
description: Recipe for adding or extending nodes on BytePlus products that do not use the ModelArk key — Seed Speech (Seed Audio, TTS, ASR, voice clone), VOD AI MediaKit (vCube, Smoothness, Image Enhance) or a new product with its own API key. Covers the key variable and client factory (Settings > BytePlus, credentials.py, @with_client), the transport with a test-replaceable _send, interrupts, uploads, async task polling, ExecutionBlocker outputs, tests, and the known Seed Speech API quirks. Use when adding a MediaKit tool, a Seed Speech endpoint, a new BytePlus product, or debugging one of these nodes.
---

# Nodes on non-ModelArk BytePlus products

Seed Speech (`nodes_speech.py` + `speech_api.py`) and AI MediaKit (`nodes_mediakit.py`) are the two worked examples. Copy whichever is closer: MediaKit for JSON task APIs (submit → poll → download), Speech for streamed/one-shot APIs. Generic V3 API questions: `comfyui-v3-nodes`.

## 1. A new product = a new key type

Keys are never interchangeable (CLAUDE.md → "Three key types"). There are no client nodes or sockets: the key lives in Settings > BytePlus / `user/.env` / the environment, and a factory hands the client to `execute`. A new product needs all of:

| Piece | MediaKit example | Notes |
|---|---|---|
| Base URLs + env var + console URL | `MEDIAKIT_REGION_BASE_URLS`, `MEDIAKIT_API_KEY_ENV`, `MEDIAKIT_API_KEYS_CONSOLE_URL` in `constants.py` | One region today; still keep the dict |
| Client object | `MediaKitClient(api_key, region)` with a `__repr__` that hides the key | Never put the key in outputs or logs |
| Client factory | `get_mediakit_client()`: `credentials.get_setting(MEDIAKIT_API_KEY_ENV)` (process environment, then `user/.env`), else a `BytePlusException` (`err_no_mediakit_key`) naming Settings > BytePlus, the variable and the `.env` path (`credentials.env_file_path()`) | Never read `os.environ` directly |
| Injection | `@with_client("mediakit_client", get_mediakit_client)` under `@classmethod` on `execute`, client as its first parameter | No client input in the schema; tests pass a fake client positionally |
| Settings > BytePlus | the variable in `credentials.CREDENTIAL_VARS` (credential id → variable; `WRITABLE_VARS` derives from it), the id in the `credential in (...)` branch of `credentials_routes.handle_save`, a `setting(...)` row with `credential: "<id>"` in `web/js/byteplus_credentials.js` | `credential_status` picks it up from `CREDENTIAL_VARS`; add the variable to `CREDENTIAL_ENV_VARS` in `tests/support.py` so tests stay isolated |
| Tests | every node in `ALL_NODES` in `tests/test_credentials.py` (no client input, `execute` wrapped) | |
| Registration | module `NODES` list → `__init__.py` `_registered_nodes` | Category `f"{GLOBAL_CATEGORY}/<Product>"` |
| Docs | README setup section for the key, CLAUDE.md layout row + "Three key types" rule + credentials variable list | |

Adding a tool/endpoint to an existing product needs only the Injection, Tests and Registration rows — then §2.

## 2. Transport

One module-level coroutine does the HTTP call and nothing else, so tests can swap it:

- MediaKit: `_send(client, method, path, body, timeout_seconds) -> (status, json)`; `mediakit_request` wraps it in `wait_interruptible`, maps `aiohttp`/timeout errors to `err_mediakit_network`, and API errors through `describe_mediakit_error` (adds the console hint on 401).
- Speech: `speech_api._send(method, url, headers, body, timeout)`; `speech_post` adds `X-Api-Key` and a fresh `X-Api-Request-Id`, and `describe_speech_error` maps numeric codes.

Rules: every await on the network goes through `nodes_shared.wait_interruptible`; sleeps use `nodes_shared.sleep_interruptible` (shared by Speech, MediaKit and the asset library — don't add per-module copies); `InterruptProcessingException` is always re-raised; all text comes from `constants.MESSAGES` with a `[BytePlus]` prefix (`nodes_shared.plain_text` strips it for progress text).

## 3. Async tasks (MediaKit pattern)

`submit_and_wait(client, path, body, node_id, submitted_key)` → `POST` → poll `GET /tasks/{id}` every `MEDIAKIT_POLL_SECONDS` with `nodes_shared.send_node_text` status under the node → `task_result`. Result URLs expire (24 h) — download inside `execute`. Synchronous tools (`/tools-sync/...`) pass a longer `timeout_seconds` instead of polling.

## 4. Media in and out

- Inputs: images/audio inline as base64 when the API takes it; otherwise upload through Comfy.org storage with the shared, cached `nodes_shared.upload_bytes_to_comfy_storage` (wrapped per product by `nodes_mediakit.upload_source` / `upload_image_source` and `nodes_speech.upload_to_comfy_storage`). It lazily imports `comfy_api_nodes.util` and needs a Comfy.org login. Build on it rather than writing a new upload path. Never register local media as a private asset.
- Validate limits **before** uploading (`validate_source_video`, `plan_image_enhance`) so the user doesn't pay for an upload that the API rejects.
- Offer both a socket and a `*_url` input where the API accepts links; `check_source` rejects both-at-once.
- Local video work uses PyAV (`_render_synced`); no OpenCV.
- An output that has nothing to show this run returns `ExecutionBlocker(None)` (downstream nodes are skipped instead of failing on `None`). If the API returns nothing because nothing needed doing, pass the source through (Smoothness).

## 5. Tests

- Speech: `tests/test_model_updates.py` / `tests/test_core_style_seed.py`, fake replacing `speech_api._send` (records requests, returns queued responses).
- MediaKit: `tests/test_mediakit.py`, `FakeMediaKit` replacing `nodes_mediakit._send` via `mock.patch.object`, scripted task statuses, `(http_status, body)` tuples for errors.
- Test request builders (`build_enhance_request`, `build_smoothness_request`, the Speech `build_*` functions) directly as pure functions; keep them module-level for that reason.
- Cover: request body, error mapping, interrupt during poll, the ExecutionBlocker/pass-through path, and limit validation. Never hit the real API — use `byteplus-live-api-probe` for that, separately.
- If the node mirrors a core node (vCube does), add a schema parity test (`test_matches_core_vcube_node`).
- A new test module that loads the pack calls `tests/support.py`'s `isolate_credentials()` from `setUpModule` (see `tests/test_mediakit.py`), so a tester's real `user/.env` never reaches the code under test.

## 6. Seed Speech quirks (confirm live before tightening)

- Host `voice.ap-southeast-1.bytepluses.com` only; ModelArk keys fail with `45000010 Invalid X-Api-Key`.
- Success code is `0` for Seed Audio and `20000000` for TTS/ASR. Standard ASR status is in the `X-Api-Status-Code` **header** (`20000001/2` pending, `20000003` silent audio), queried with the same `X-Api-Request-Id` as the submit.
- TTS: model goes in `X-Api-Resource-Id`; `additions` is a JSON **string**; the body is a stream of JSON objects with base64 PCM (`iter_json_objects`). Subtitle chunks are undocumented.
- Seed Audio subtitles arrive as `sentences` or `utterances`.
- `seed_speech_voices.py` is generated from the official voice list page (URL and date in its header). Speaker IDs are saved in workflows: **append** new voices, never rename or remove; update the header date.
- Docs mirror and a working reference client: see the `local-comfyui-test-env` memory.
