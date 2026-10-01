---
name: byteplus-model-update
description: Step-by-step workflow for adding, re-dating, retiring or region-limiting a model in this BytePlus node pack — Seedream, Seedance 1.x/2.x/2.5, the BytePlus LLM node (Seed, DeepSeek, GLM), Seed Speech models and MediaKit tool versions. Use whenever BytePlus announces a new model or model version, a deprecation/shutdown notice, a regional deactivation, or the user says "add model X", "X is retired", "bump the model ID", even if they don't mention models_config.py.
---

# Model add / update / retire

`models_config.py` is the catalog; most bugs come from a map that was updated while a sibling table was not. Work through the section that matches the change, then the **Finish** list. Background on how models flow through requests: `byteplus-node-maintenance`.

## 0. Confirm the facts first

- Get the exact dated model ID, regions, limits and parameters from BytePlus: `arkcli models list --modality image|video|text` (`arkcli-models` skill) or the docs (`arkcli-docs` skill). Never copy an ID from ComfyUI core — core uses Comfy.org proxy aliases (`seedream-5-0-pro-…` vs BytePlus `dola-seedream-5-0-pro-…`).
- If a behaviour is unclear from the docs (accepted values, media types, effort levels), check it live before encoding it — see `byteplus-live-api-probe`. Record what was checked live in a comment next to the table entry (existing examples: `SEEDANCE_DRAFT_FINAL_RESOLUTIONS`, `SEED_LLM_MODEL_MAP`).

## 1. New dated version of an existing model

Change only the value in the map (`VIDEO_MODEL_MAP`, `SEEDREAM_*_MODEL_MAP`, `VISUAL_MODEL_MAP`, `SEED_LLM_MODEL_MAP`, core-style `SEEDREAM_MODELS` / `SEEDANCE_1_MODELS` …). UI names are saved in workflows — keep them. Then grep the old ID across the repo (`git grep <old-id>`): tests, README and example workflows often hard-code it.

## 2. New model

Add it to **every** table of its family:

| Family | Tables |
|---|---|
| Seedance 2.x | `VIDEO_MODEL_MAP`, `VIDEO_2_UI_OPTIONS`, `VIDEO_2_MODEL_RESOLUTIONS`, `VIDEO_2_MODEL_MAX_DURATIONS`, `VIDEO_2_MODEL_REFERENCE_LIMITS`, `SEEDANCE2_CORE_MODEL_OPTIONS` (label → key; add a `… Draft` label + `SEEDANCE2_CORE_DRAFT_OPTIONS` if it drafts), `SEEDANCE2_REF_VIDEO_DOWNSCALE_TARGETS` if core has budgets for it |
| Seedance 2.5-family | also `SEEDANCE_2_5_FAMILY` and `SEEDANCE_DRAFT_FINAL_RESOLUTIONS` — missing ones raise KeyError while building the schema and **the whole pack fails to load** |
| Seedance 1.x | `VIDEO_MODEL_MAP`, `VIDEO_1_UI_OPTIONS`, `SEEDANCE_1_MODELS` (+ FLF options if it supports first/last frame) |
| Seedream | Legacy `SEEDREAM_4/5_MODEL_MAP` + core-style `SEEDREAM_MODELS`, `SEEDREAM_MODEL_CAPS` (pixel range, refs, outputs); layer separation in `SEEDREAM_LAYER_SEPARATION_MODELS`; size presets in `nodes_image_schema.py` |
| LLM (`BytePlusSeed`) | `SEED_LLM_MODEL_MAP` (core's three labels stay first — first option is the default); `SEED_LLM_NO_REASONING_EFFORT` if it rejects `reasoning.effort`. Non-Seed LLMs are fine if ModelArk hosts them and they take the same request |
| Legacy Visual | `VISUAL_MODEL_MAP` (first entry = default) |
| Seed Speech | `SEED_AUDIO_MODELS` / `SEED_TTS_MODELS` / `SEED_ASR_MODELS` (TTS model = `X-Api-Resource-Id`) |
| MediaKit | tool-version inputs in `nodes_mediakit.py` and `IMAGE_VERSION_LIMITS` |

Then check logic that keys on substrings or UI constants rather than tables:
- `"seedance-2-"` in `executor.py` (request policy, 64 MiB limit, time estimate).
- `SEEDREAM_4_0_UI_MODEL`, `SEEDREAM_5_PRO_UI_MODEL`, `SEEDREAM_5_URL_MODELS` branches in `nodes_image.py`.
- JS: `git grep -n "<family prefix>" web/js` (e.g. `"seedream-4-0"` drives `prompt_optimization` visibility).
- Quota: `quota.py` builds its model list from the maps; check the token estimate if pricing differs.
- Validation for new limits next to the `validate_seedance2_*` helpers in `nodes_video.py`.

Deliberate differences from core (BytePlus-only model, different limits) go in the deviation sets of the core-parity tests — see `byteplus-core-parity-sync`.

## 3. Retire a model

1. Remove it from every map and option list above.
2. Add `"<ui-name>": ("<model-id>", "<replacement-model-id>")` to `RETIRED_MODELS`, with the notice date in the comment. `RETIRED_VIDEO_UI_OPTIONS` / `RETIRED_SEED_UI_OPTIONS` derive from its prefix — a new prefix needs a new derived list.
3. Legacy nodes keep the name in their combo (so saved workflows load) and call `core_style.raise_if_model_retired(model)` before any request. Never change a Legacy node's inputs.
4. Core-style nodes simply drop it; if core still lists it, add the input to the test's deviation set (see `DEPRECATED_MODEL` in `tests/test_core_style_seedance1.py`).
5. `QUERY_TASKS_MODEL_LIST` keeps retired video models last so old tasks stay queryable.
6. Example workflows using it must switch to the replacement.

## 4. Region-limited model

Add `"<model-id>": ("<region>",)` to `MODEL_REGION_EXCLUSIONS` and make sure the node calls `core_style.raise_if_model_unavailable_in_region(client, model_id)`. Don't remove the model from the UI.

## Finish

- [ ] User-facing text in `constants.MESSAGES` (tooltips, errors); every `get_text` key exists.
- [ ] Node `description` / tooltip strings that list model names.
- [ ] `README.md` model list (and the "formerly …" note if a display name changed — node IDs never change).
- [ ] `CLAUDE.md` layout row if a node's scope changed.
- [ ] Tests: `tests/test_model_updates.py` and the matching `tests/test_core_style_*.py` (expected option lists, ID maps, request payload per new model). Use `subTest` per model.
- [ ] Example workflows if a default changed → `comfyui-release-and-testing` §2.
- [ ] Run tests (both commands in CLAUDE.md); report skipped tests as skipped.
