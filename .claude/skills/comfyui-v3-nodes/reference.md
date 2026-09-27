# V3 API signature reference

Source: Comfy-Org/ComfyUI `comfy_api/latest/_io.py`, `_ui.py`, `__init__.py`, `execution.py`, `nodes.py` @ `4ef23c34` (2026-09-26). "(vX)" = first ComfyUI release containing it.

## `io.Schema` (dataclass)

| Field | Default | Notes |
|---|---|---|
| `node_id` | required | Globally unique; prefix it (`BytePlus…`). |
| `display_name` | `None` | |
| `category` | `"sd"` | This pack: `GLOBAL_CATEGORY`. |
| `inputs` / `outputs` / `hidden` | `[]` | |
| `description` | `""` | Tooltip on the node. |
| `search_aliases` | `[]` | (v0.11.0) |
| `is_input_list` | `False` | Receive lists instead of per-item calls. |
| `is_output_node` | `False` | Always executed; auto-adds `prompt`, `extra_pnginfo` hidden. |
| `is_deprecated` / `is_experimental` | `False` | Badges in the UI. Keep deprecated nodes registered so old workflows load. |
| `is_dev_only` | `False` | (v0.11.1) Hidden unless dev mode. |
| `is_api_node` | `False` | Auto-adds Comfy.org auth hidden inputs; frontend treats it as a Comfy partner node. **Don't set it for this pack** (users don't need a Comfy login). |
| `not_idempotent` | `False` | Disables cross-node cache reuse. |
| `enable_expand` | `False` | Required to return `NodeOutput(expand=...)`. |
| `accept_all_inputs` | `False` | (v0.11.0) Pass undeclared prompt inputs as kwargs. |
| `price_badge` | `None` | (v0.9.2) `io.PriceBadge(expr=<JSONata>, depends_on=io.PriceBadgeDepends(widgets=[...], inputs=[...], input_groups=[...]))`. Nested widget names are dotted (`"model_version.resolution"`). Unknown names raise at schema time. |
| `essentials_category` | `None` | (v0.15.0) |
| `has_intermediate_output` | `False` | (v0.19.0) |
| `loop_boundary` | `None` | (v0.36.0) `"start"`/`"end"`. |

`Schema.validate()` rejects duplicate input/output ids. Outputs without id get `_{i}_{io_type}_`.

## Inputs

Common: `Input(id, display_name=None, optional=False, tooltip=None, lazy=None, extra_dict=None, raw_link=None, advanced=None)`. Widget inputs add `default, socketless, widget_type, force_input`. `advanced=True` (v0.10.0) → collapsible "advanced" area.

| Type | Extra kwargs |
|---|---|
| `io.Boolean.Input` | `default, label_on, label_off` |
| `io.Int.Input` | `default, min, max, step, control_after_generate: bool \| io.ControlAfterGenerate, display_mode: io.NumberDisplay.number/slider/gradientslider` |
| `io.Float.Input` | `default, min, max, step, round, display_mode, gradient_stops` |
| `io.String.Input` | `multiline=False, placeholder, default, dynamic_prompts` |
| `io.Combo.Input` | `options: list[str] \| list[int] \| Enum, default, control_after_generate, upload: io.UploadType.image/audio/video/model, image_folder, remote: io.RemoteOptions` |
| `io.MultiCombo.Input` | `options, default: list[str], placeholder, chip` |
| `io.Image/Mask/Video/Audio/Latent.Input` | socket only |
| `io.Custom("MY_TYPE").Input/Output` | custom socket type (this pack: `BYTEPLUS_CLIENT`) |
| `io.AnyType` | `"*"` |
| `io.MultiType.Input(id \| <Input>, types=[...])` | Accepts any listed type; passing an Input instance reuses its widget config. |
| `io.MatchType.Template("T", allowed_types=...)` + `io.MatchType.Input(id, template=T)` / `io.MatchType.Output(template=T)` | Output type follows the connected input. |

Outputs: `X.Output(id=None, display_name=None, tooltip=None, is_output_list=False)`.

## Dynamic inputs

- `io.DynamicCombo.Input(id, options=[io.DynamicCombo.Option(key, [inputs])], display_name, optional, tooltip, lazy, extra_dict)` — options may nest DynamicCombos. Value: `{id: selected_key, child_id: value, ...}`.
- `io.Autogrow.Input(id, template=io.Autogrow.TemplateNames(input, names=[...], min=1) | io.Autogrow.TemplatePrefix(input, prefix, min=1, max=10))` — widget templates are forced to sockets. Value: dict of connected slots.
- `io.DynamicSlot.Input(slot=<Input>, inputs=[...])` — extra inputs appear when `slot` is connected.
- History: stubs v0.3.48 → implemented v0.4.0 → public + wired into execution **v0.8.0** → Autogrow validation fix v0.10.0.

## Hidden (`io.Hidden`)

`unique_id`, `prompt`, `extra_pnginfo`, `dynprompt`, `auth_token_comfy_org`, `api_key_comfy_org`, `comfy_usage_source` (v0.25.0), `execution_list` (internal — do not use). Read via `cls.hidden.<name>`; filled from the prompt's `extra_data` (`execution.py`).

## Return values

`io.NodeOutput(*values, ui=None, expand=None, block_execution=None)`. `execute` may also return `None`, a tuple, or a dict — all normalized. `block_execution="msg"` stops downstream nodes (ExecutionBlocker).

`ui` helpers (`from comfy_api.latest import ui`): `PreviewImage(image, animated=False, cls=None)`, `PreviewMask`, `PreviewAudio(audio, cls=None)`, `PreviewVideo([SavedResult])`, `PreviewText(str)`, `SavedResult(filename, subfolder, io.FolderType.output|input|temp)`, `SavedImages`, `ImageSaveHelper`, `AudioSaveHelper`.

## Lifecycle classmethods (any may be `async def`)

- `define_schema() -> Schema` — called at load **and again on every `INPUT_TYPES()` call** (each `/object_info` request and prompt validation), so inputs can change at runtime (this pack re-reads `api_keys.json` there → new keys appear after a browser refresh). Output types and flags are cached on the class after the first `GET_SCHEMA()`. Keep it cheap and side-effect free.
- `execute(**inputs) -> NodeOutput` — sync or async (`inspect.iscoroutinefunction`).
- `validate_inputs(**kw) -> bool | str` — constant values only; named inputs skip built-in validation; an `input_types` param skips link type checks.
- `fingerprint_inputs(**kw) -> Any` — cache key; exception → NaN → always rerun.
- `check_lazy_status(**kw) -> list[str]` — names of lazy inputs still needed.

## Registration (`nodes.py:2302-2345`)

```python
class BytePlusExtension(ComfyExtension):
    async def on_load(self) -> None: ...                       # optional
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return _registered_nodes
async def comfy_entrypoint() -> BytePlusExtension:
    return BytePlusExtension()
WEB_DIRECTORY = "./web"   # or [tool.comfy] web = "web" in pyproject
```

Nodes register under `schema.node_id`. `NODE_CLASS_MAPPINGS` wins over `comfy_entrypoint` if both exist.

## Execution model

- Each prompt runs in `asyncio.run` on the executor thread. An async `execute` becomes a task; if not done after one `sleep(0)` the node is PENDING and other ready nodes run → **independent async nodes run concurrently** (e.g. two Seedance nodes in one graph).
- `comfy.utils.ProgressBar(total, node_id=None).update_absolute(v, total, preview)` also checks interrupts (global hook in `main.py`).
- `ComfyAPI().execution.set_progress(value, max_value, node_id=None, preview_image=None, ignore_size_limit=False)` (async; `ComfyAPISync` for sync).
- `PromptServer.instance.send_progress_text(text, node_id)`; `PromptServer.instance.send_sync(event, data, sid=None)` (None = broadcast).
- `comfy.model_management.processing_interrupted()` / `throw_exception_if_processing_interrupted()` → `InterruptProcessingException(BaseException)`.

## Video (`comfy_api.latest.Input.Video`, `InputImpl`, `Types`)

- `InputImpl.VideoFromFile(str | BytesIO, *, start_time=0, duration=0, crop=None)`, `InputImpl.VideoFromComponents(Types.VideoComponents(images, frame_rate: Fraction, audio=None, metadata=None, alpha=None))`, `VideoFromList`.
- Methods: `get_components()`, `save_to(path|BytesIO, format=Types.VideoContainer.AUTO, codec=Types.VideoCodec.AUTO, metadata=None, bit_depth=None, crf=None, color_space=None, preset=None)`, `get_dimensions()`, `get_duration()`, `get_frame_count()`, `get_frame_rate()`, `get_container_format()`, `get_stream_source()`, `as_trimmed()`, `as_cropped()`.
- `Types.VideoContainer`: AUTO, MP4, MKV, WEBM. `Types.VideoCodec`: AUTO, H264, AV1.

## Docs

- https://docs.comfy.org/custom-nodes/v3_migration (matches code, except it implies `v0_0_2` pinning is stable — it isn't).
- Real-world examples in core: `comfy_extras/nodes_logic.py` (DynamicCombo, MatchType, lazy), `comfy_api_nodes/nodes_bytedance.py` (DynamicCombo + Autogrow + PriceBadge).
