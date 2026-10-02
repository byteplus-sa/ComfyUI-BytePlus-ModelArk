---
name: byteplus-core-parity-sync
description: Bring this pack's core-style nodes back in line with ComfyUI core's ByteDance partner nodes (comfy_api_nodes/nodes_bytedance.py, nodes_bytedance_llm.py) after core changes — new inputs, renamed tooltips, new models, new nodes, changed defaults or validations. Use when ComfyUI releases a new version, a core-parity test (test_matches_core_*) fails, the user asks "what changed in core's ByteDance nodes", or before raising the minimum ComfyUI version.
---

# Core parity sync

The core-style nodes copy core's ByteDance nodes input for input (rule in CLAUDE.md → "Core-style nodes"). This skill is the procedure for catching up after core moves. Payload shapes, ID differences and patterns not to copy: `byteplus-node-maintenance/core-partner-nodes.md`.

## 1. See what changed in core

```bash
# Commits touching core's ByteDance files since the last sync
for f in nodes_bytedance.py nodes_bytedance_llm.py apis/bytedance.py; do
  gh api "repos/Comfy-Org/ComfyUI/commits?path=comfy_api_nodes/$f&per_page=20" \
    --jq '.[] | "\(.commit.author.date[:10]) \(.sha[:9]) \(.commit.message|split("\n")[0])"'
done
# Diff one commit
gh api repos/Comfy-Org/ComfyUI/commits/<sha> --jq '.files[] | select(.filename|test("bytedance")) | .patch'
```

Last sync date: the "Read on" date in `core-partner-nodes.md`. Update it when you finish.

## 2. Run the parity tests against the new core

Point `COMFYUI_ROOT` at a checkout of the new ComfyUI (`git -C <root> checkout <tag>`); interpreter and paths are in the `local-comfyui-test-env` memory.

```bash
COMFYUI_ROOT=<root> <python> -m unittest tests.test_core_style_seedream tests.test_core_style_seedance1 \
  tests.test_core_style_seedance2 tests.test_core_style_seed tests.test_mediakit tests.test_credentials -v
```

How each file compares:

| Test | Compares with core by |
|---|---|
| `test_core_style_seedream` (`test_matches_core_schema`) | importing `comfy_api_nodes.nodes_bytedance` live; exceptions in `BYTEPLUS_LIMIT_DEVIATIONS` |
| `test_core_style_seedance1` (`test_matches_core_nodes`) | live import; exceptions in `BYTEPLUS_DEVIATIONS`, `REMOVED_CORE_INPUTS`, `DEPRECATED_MODEL` |
| `test_core_style_seedance2` (`test_matches_core_nodes`) | live import; expected order in `MODEL_LABELS`, `EXTRAS`, `REFERENCE_TAIL` |
| `test_core_style_seed` (`test_matches_core_llm_node_live`) | live import of `ByteDanceSeedNode`: core's inputs, model options and their children; the other tests in the file pin this pack's extras with hard-coded values |
| `test_mediakit` (`test_matches_core_vcube_node`) | `GET_NODE_INFO_V1()` of `ByteDanceVideoEnhanceNode` |

A skip ("core ByteDance nodes unavailable") is not a pass — fix the env. CI (`.github/workflows/tests.yml`) runs this suite on every push and weekly against ComfyUI's latest release and `master`, and fails on any skip, so a failing scheduled run is usually the first sign that core moved.

## 3. Classify every difference

For each failing assertion or diff hunk, decide one of:

1. **Follow core** — input added/renamed/reordered, tooltip or description text, default, `advanced` flag, DynamicCombo option, output order, validation message. Apply it on the core-style node (never on a Legacy node).
2. **BytePlus deviation** — the BytePlus docs say otherwise (limits, reference counts, model availability, durations). Keep ours, add the input to the test's deviation set with a one-line comment citing the BytePlus rule.
3. **Not applicable** — proxy-only behaviour (`is_api_node`, `price_badge`, Comfy credits, `/proxy/...` virtual-library assets, `sync_op`/`poll_op`). Ignore; mention it in the summary.
4. **New core node** — mirror it as a new `BytePlus…` node with the same split: `client` socket first, core's inputs, this pack's extras last with `advanced=True` **and** `optional=True`, extra outputs after core's. Register in the module's `NODES` (picked up by `__init__.py`), add a parity test.
5. **New core model** — confirm the BytePlus ID and follow `byteplus-model-update`. Core's ID is a proxy alias.

Keep these invariants while editing:
- Node IDs never change; display names may (add the old name to `search_aliases`).
- Inputs a frontend rule keys on: `git grep -n "<input name>" web/js`; renaming one silently breaks visibility rules.
- An input order change shifts `widgets_values` in every example workflow that uses the node → `comfyui-release-and-testing` §2.

## 4. Raising the ComfyUI floor

Only when a parity change needs a newer core API (a new `comfy_io` type, a new Schema field). Then: `requires-comfyui` in `pyproject.toml`, the two README mentions, and recheck the lazy `comfy_api_nodes.util.upload_*_to_comfyapi` signatures we wrap (`nodes_video.upload_video_to_comfy_storage`, `nodes_shared.upload_bytes_to_comfy_storage`, `nodes_assets.upload_image_to_comfy_storage` — `upload_image_to_comfyapi` with `total_pixels` — and `nodes_assets.upload_asset_audio`) — that module is not a stable API.

## 5. Finish

- Full test run (CLAUDE.md commands), Legacy-node tests included.
- Smoke-load changed templates in Classic Canvas and Nodes 2.0.
- Update the "Read on" date and anything stale in `core-partner-nodes.md` (ID table, payload notes).
- Summarise for the user: followed / deviations kept / ignored / new nodes, with core commit SHAs.
