# Frontend extension API reference

Source: Comfy-Org/ComfyUI_frontend `main`, 2026-09-27. Types: npm `@comfyorg/comfyui-frontend-types` (types only; runtime still via the shims). Official docs: https://docs.comfy.org/custom-nodes/js/javascript_overview (+ `javascript_hooks`, `javascript_settings`, `context-menu-migration`), and the repo's `docs/extensions/*.md` migration notes.

## `app.registerExtension({...})` — `ComfyExtension` (`src/types/comfy.ts`)

`app` is always appended as the last argument.

| Hook | Signature / notes |
|---|---|
| `init(app)` | Before node registration. |
| `addCustomNodeDefs(defs, app)` | Mutate raw node defs. |
| `getCustomWidgets(app)` | `→ Record<type, (node, inputName, inputData, app) => {widget}>`; called at `registerExtension` time. |
| `beforeRegisterNodeDef(nodeType, nodeData, app)` | Patch `nodeType.prototype` per node class (prototype hijacking works but docs call it "subject to change"). |
| `beforeRegisterVueAppNodeDefs(defs[], app)` | Mutate defs in place for the Vue app. |
| `registerCustomNodes(app)` | Register frontend-only nodes. |
| `setup(app)` | After registration; register websocket listeners here. |
| `nodeCreated(node, app)` | Every node instance (new or loaded) — before saved values are applied. |
| `loadedGraphNode(node, app)` | Nodes from a loaded workflow, after configure. |
| `beforeLoadGraph` / `afterLoadGraph` | Around a workflow load. |
| `beforeConfigureGraph(graphData, missingNodeTypes, app)` / `afterConfigureGraph(missingNodeTypes, app)` | "Workflow loaded" → use `afterConfigureGraph`. |
| `onGraphLoadError(error, app)` | |
| `onNodeOutputsUpdated(nodeOutputs)` | |
| `getNodeMenuItems(node)` / `getCanvasMenuItems(canvas)` | Return `(IContextMenuValue\|null)[]`; replaces monkey-patching `getExtraMenuOptions`/`getCanvasMenuOptions` (now logs `[DEPRECATED]`). |
| `getSelectionToolboxCommands(item)` | `→ string[]` command ids. |

Declarative fields: `commands`, `keybindings`, `menuCommands: [{path, commands}]`, `settings: [{id, name, type, defaultValue, category?, onChange(new, old)}]`, `bottomPanelTabs`, `aboutPageBadges: [{label, url, icon}]`, `topbarBadges`, `actionBarButtons`.

Runtime: `app.extensionManager.toast.add({severity, summary, detail, life})`, `.dialog` (prompt/confirm), `.setting.get(id)` / `.set(id, v)`, `.command.execute(id)`, `.registerSidebarTab(tab)`, `.lastNodeErrors`, `.lastExecutionError`.

Deprecated `app` getters → replacements: `app.graph` → `app.rootGraph`; `app.lastNodeErrors`/`lastExecutionError` → `app.extensionManager.*`; `app.runningNodeId`, `app.progress` → execution store; `app.widgets` → widget store; `app.ui.settings.getSettingValue` → `app.extensionManager.setting.get`. Safe accessors: `app.canvasOrUndefined`, `app.rootGraphOrUndefined`, `app.isGraphReady`.

## LGraphNode / widgets (`src/lib/litegraph/src/LGraphNode.ts`, `widgets/BaseWidget.ts`)

- `node.addWidget(type, name, value, callback | null, options?)` (combo needs `options.values`), `node.addCustomWidget(w)`, `node.addDOMWidget(name, type, element, {getValue, setValue, getMinHeight, getMaxHeight, getHeight, hideOnZoom, onDraw, afterResize, margin})`, `node.removeWidget(w)`.
- `node.widgets` may be `undefined` → `node.widgets ??= []`. Widget names must be unique per node and never renamed after creation (values are keyed by name in `widgetValueStore`).
- Visibility (`main` / newer frontends): `widget.hidden = bool` (setter → `suppression.byExtension`; all surfaces), `widget.advanced = bool`; declarative `options.hidden / advanced / canvasOnly / hideInPanel / surfaces`. Legacy types `"hidden"`, `"converted-widget"`, `"tschide*"` still recognised. **On 1.52.7 there is no setter**: canvas honours a plain `widget.hidden` flag, Vue nodes read `options.hidden` from a non-reactive snapshot, so extensions cannot hide Vue widgets at runtime.
- Size: `node.computeSize()`, `node.setSize([w, h])`; `node.size` = requested (serialized) size, `node.getBounding()` = rendered. `node.graph?.setDirtyCanvas(true, true)` to redraw.
- Callbacks: `onConfigure(serialised)` (shallow copy), `onSerialize(o)`, `onConnectionsChange(type, index, isConnected, link_info, inputOrOutput)` (5th arg may be a `SubgraphIO`), `onWidgetChanged(name, value, old, widget)`, `onResize`, `onAdded`, `onRemoved`, `onExecuted(output)` (on `executed`), `onExecutionStart()`.
- Widget→input conversion (`convertWidgetToInput`) is deprecated and unnecessary — widgets and sockets co-exist.
- Graph events: `graph.events` emits `node:added`, `node:before-removed`, `node:removed`.

## Vue nodes ("Nodes 2.0")

Setting `Comfy.VueNodes.Enabled` (experimental; default on for new Desktop/Cloud installs ≥ 1.41.0). Known widget types render as Vue components; custom canvas-drawn widgets fall back to `WidgetLegacy.vue` (drawn on a mini canvas); DOM widgets via `WidgetDOM`. Vue nodes honour `widget.hidden`. `onDrawForeground` custom drawing does not appear on Vue nodes.

## Native V3 dynamic widgets (`src/core/graph/widgets/dynamicWidgets.ts`)

`COMFY_DYNAMICCOMBO_V3` (child widgets `<combo>.<child>`, `restoreRemovedValues` when switching back, preserves links, resizes), `COMFY_AUTOGROW_V3` → `applyAutogrow`, `COMFY_MATCHTYPE_V3` → `applyMatchType`.

## Websocket / API (`src/scripts/api.ts`)

`api.addEventListener(type, (e) => e.detail)`; `api.removeEventListener`. Custom server events need a registered listener or the client logs `Unknown message type X` once.

| Event | `detail` |
|---|---|
| `status` | `{status: {exec_info: {queue_remaining}} \| null, sid?}` |
| `progress` | `{value, max, prompt_id, node}` |
| `progress_state` | `{prompt_id, nodes: {[id]: {value, max, state: 'pending'\|'running'\|'finished'\|'error', node_id, prompt_id, display_node_id?, parent_node_id?, real_node_id?}}}` — drives built-in bars |
| `progress_text` | `{nodeId, text, prompt_id?}` — shown under the node |
| `executing` | `{node \| null, display_node?, prompt_id}` |
| `executed` | `{node, display_node, output, merge?, prompt_id}` |
| `execution_start` / `execution_success` | `{prompt_id, timestamp}` |
| `execution_cached` | `{nodes[]}` |
| `execution_error` | `{node_id, node_type, exception_message, exception_type, traceback[], executed[]}` |
| `execution_interrupted` | |
| `b_preview`, `b_preview_with_metadata` | preview blobs |

Subgraph node ids are colon paths (`"12:34"`); compare with `display_node_id`/`node` accordingly.

Routes: `api.fetchApi(route, opts)` (prefixes `/api`, adds auth on Cloud), `api.apiURL(route)`, `api.fileURL(route)`. `api.dispatchEvent` deprecated → `api.dispatchCustomEvent`.

Python side: `PromptServer.instance.send_sync(event, data, sid=None)` (None = broadcast), `PromptServer.instance.send_progress_text(text, node_id)`.
