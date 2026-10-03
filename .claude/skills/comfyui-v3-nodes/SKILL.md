---
name: comfyui-v3-nodes
description: ComfyUI V3 backend node API (comfy_api.latest io/ui) reference for this pack — io.Schema fields, input/output types, DynamicCombo/Autogrow/MultiType/DynamicSlot value shapes, Hidden inputs, validate_inputs/fingerprint_inputs, async execute, progress and interrupt handling, VideoInput, registration via ComfyExtension, and version compatibility. Use when adding or changing a node, input, output, or execute() logic in nodes/*.py, or when debugging how ComfyUI passes values into a node.
---

# ComfyUI V3 node API

Verified against Comfy-Org/ComfyUI `master` @ `4ef23c34` (v0.37.0, 2026-09-26). This pack imports `from comfy_api.latest import io as comfy_io` (and `ComfyExtension` in `__init__.py`). Everything below uses `io.` — in this repo write `comfy_io.`.

For the full signature list see [reference.md](reference.md).

## Node skeleton

```python
from comfy_api.latest import io as comfy_io

class BytePlusThing(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusThing",          # == class name here; part of saved workflows, never rename
            display_name="BytePlus Thing",
            category=GLOBAL_CATEGORY,          # "BytePlus ModelArk"
            inputs=[...], outputs=[...],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.prompt],
            is_output_node=False,
        )

    @classmethod
    @with_client("client", get_client)   # this pack: the client is no input, the decorator injects it
    async def execute(cls, client, prompt, **kwargs):   # async: runs concurrently with other ready nodes
        node_id = cls.hidden.unique_id
        ...
        return comfy_io.NodeOutput(video, last_frame, json_text)
```

Then add the class to its module's `NODES` list (registered by `__init__.py`) and to `ALL_NODES` in `tests/test_credentials.py`.

## Rules that bite

- **`execute` runs on a locked clone of the class.** Assigning `cls.something = ...` inside `execute` raises `AttributeError`. Keep per-node state in module-level dicts keyed by `cls.hidden.unique_id` (this repo's pattern: `NON_BLOCKING_TASK_CACHE`, `SEED_LAST_RESPONSES`).
- **Hidden values must be declared** in `Schema(hidden=[...])`; undeclared ones read as `None` (no error). `is_output_node=True` auto-adds `prompt` + `extra_pnginfo`; `is_api_node=True` auto-adds the Comfy.org auth fields.
- **Only `NODE_CLASS_MAPPINGS` *or* `comfy_entrypoint` is used per module** — if a module defines both, `comfy_entrypoint` is silently ignored. This pack uses only `comfy_entrypoint`; never add `NODE_CLASS_MAPPINGS`.
- **An exception in `comfy_entrypoint`/`define_schema` only logs a warning and skips the whole pack.** After schema edits, check the ComfyUI console for "Error while calling comfy_entrypoint".
- **A `Schema` kwarg newer than the user's ComfyUI raises `TypeError` → pack skipped.** Our floor is ComfyUI 0.31.0 (README; raised for the LAYERS type used by Layer Separation); don't use fields added later (e.g. `loop_boundary` 0.36.0) without raising the floor.
- **Blocking I/O in `async def execute` stalls every node.** Use `aiohttp` or `asyncio.to_thread(...)` (this repo wraps the sync Ark SDK with `asyncio.to_thread`).
- **Output ids are part of saved workflows too** — don't reorder or rename outputs casually.

## Dynamic inputs — how values arrive

| Input | Wire/prompt key | Value in `execute` |
|---|---|---|
| `io.DynamicCombo.Input("model", options=[io.DynamicCombo.Option("m1", [inputs...]), ...])` | `model`, `model.<child>` | one dict: `{"model": "m1", "<child>": value, ...}` — **only the selected option's children are present**, use `.get()` |
| `io.Autogrow.Input("images", template=io.Autogrow.TemplateNames(io.Image.Input("image"), names=[...], min=0))` | `images.<name>` | `dict[name → value]` of **connected slots only**; `{}` when none |
| `io.Autogrow.TemplatePrefix(input, prefix="img", min=1, max=10)` | `img0 … img{max-1}` | same; hard cap 100; slots below `min` are required |
| `io.MultiType.Input("media", types=[io.Image, io.Video])` | `media` | whichever type is connected — branch on `isinstance` |
| `io.DynamicSlot.Input(slot=..., inputs=[...])` | nested | nested dict; always optional |

Autogrow can sit inside a DynamicCombo option: `model["reference_images"]` → `{"image_1": tensor, ...}`. In saved workflow JSON a DynamicCombo input serializes with type `COMFY_DYNAMICCOMBO_V3` and child inputs `model.<name>` (see `tests/test_workflow_templates.py`).

Minimum ComfyUI for working DynamicCombo/Autogrow: **0.8.0** (public API), safer **≥ 0.10.0** (Autogrow validation fix).

## Validation and caching

- `validate_inputs(cls, **kw) -> True | str` runs at queue time with **constant widget values only** (linked inputs are `None`). Any input it names **skips built-in min/max/combo checks** — so re-check those yourself if you name them. Return a string to reject with that message.
- `fingerprint_inputs(cls, **kw)` = V1 `IS_CHANGED`: equal result → cached output reused. Raising → always re-runs.
- Outputs are cached by input signature. API nodes normally rely on a `seed` widget with `control_after_generate=True` to force re-runs; `not_idempotent=True` only stops cross-node cache sharing.
- `check_lazy_status` only matters for inputs declared `lazy=True`.

## Progress and interrupts

- Numeric progress bar (both renderers): `from comfy_execution.progress import get_progress_state; get_progress_state().update_progress(node_id, value, max)` — this repo's `executor._update_node_progress` does that **plus** a legacy `PromptServer.instance.send_sync("progress", {...})`. V3-native alternative: `await ComfyAPI().execution.set_progress(value, max_value, node_id=...)` (`from comfy_api.latest import ComfyAPI`). Pass `node_id` explicitly from worker threads.
- Status text under the node: `PromptServer.instance.send_progress_text(text, node_id)` (frontend `progress_text` event) — used by core API nodes for "Status: running / Time elapsed".
- Interrupts: `comfy.model_management.throw_exception_if_processing_interrupted()` raises `InterruptProcessingException` (a **`BaseException`**, so bare `except Exception` won't catch it, but `except BaseException` will). `processing_interrupted()` checks without raising. Poll one of these at least every ~1 s in any wait loop, cancel remote work, and **always re-raise**.

## Media types

- `IMAGE` = `torch.Tensor [B,H,W,C]` float 0–1; `MASK` = `[B,H,W]`. No public tensor⇄PIL helper in `comfy_api.latest`; convert manually.
- `VIDEO` = `comfy_api.latest.Input.Video` (abstract). Build from files with `InputImpl.VideoFromFile(path_or_BytesIO)` (this repo imports `VideoFromFile` from `comfy_api.input_impl`). Useful methods: `save_to(path|BytesIO, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)`, `get_duration()`, `get_dimensions()`, `get_frame_rate()`, `get_components()`, `get_stream_source()`.
- `AUDIO` = `{"waveform": Tensor[B,C,T], "sample_rate": int}`.
- Previews: `NodeOutput(..., ui=ui.PreviewImage(img, cls=cls))`, `ui.PreviewText(str)`, `ui.PreviewVideo([ui.SavedResult(file, subfolder, io.FolderType.output)])` (PreviewVideo does not save; save first).

## Renaming or replacing a node without breaking workflows

Only matters once the pack has shipped (until then, rename freely). Keep the old class registered with `is_deprecated=True`, or register a replacement (Node Replacement API, ComfyUI PR #12014, Feb 2026) in the extension's `on_load`:

```python
from comfy_api.latest import ComfyAPI, ComfyExtension, io

class BytePlusExtension(ComfyExtension):
    async def on_load(self) -> None:
        await ComfyAPI().node_replacement.register(io.NodeReplace(
            new_node_id="BytePlusNew", old_node_id="BytePlusOld",
            old_widget_ids=[...],      # positional widgets_values index -> input id of the OLD node
            input_mapping=[...], output_mapping=[...]))
```

## Versioning

`comfy_api.latest`, `v0_0_2`, `v0_0_1` all have `STABLE = False`, and the pinned versions re-export the **same** `io`/`ui` as `latest` — pinning gives no isolation. Changes are mostly additive; watch the ComfyUI changelog when raising the floor.
