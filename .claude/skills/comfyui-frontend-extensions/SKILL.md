---
name: comfyui-frontend-extensions
description: ComfyUI frontend JavaScript extension API for this pack's web/js files — app.registerExtension hooks and order, widget show/hide (widget.hidden) that works on both Classic Canvas and Nodes 2.0 (Vue nodes), node resizing, native V3 DynamicCombo/Autogrow handling, websocket events (progress, progress_state, progress_text, executed), deprecated patterns to avoid, and how to debug. Use when editing web/js/byteplus_dynamic_widgets.js or byteplus_progress.js, adding widget visibility rules, or when a widget/progress UI misbehaves in one renderer.
---

# ComfyUI frontend extensions

Verified against Comfy-Org/ComfyUI_frontend `main` (2026-09-27; releases v1.53–1.55, `@comfyorg/comfyui-frontend-types` 1.56.0) **and live on frontend 1.52.7**, the version ComfyUI 0.37.x pins. Several APIs below exist only on newer frontends — always feature-detect. Full API list: [reference.md](reference.md).

## This pack's JS

| File | Extension name | Does |
|---|---|---|
| `web/js/byteplus_dynamic_widgets.js` | `ComfyUI.BytePlus.DynamicWidgets` | Show/hide rules (`TARGET_WIDGETS`, `widgetLogic`, run from chained `widget.callback` and `applyAllWidgetRules` after load), Autogrow input relabels (`AUTOGROW_LABEL_RULES`), DynamicCombo value restore after load (`restoreDynamicComboWidgetValues`, nodes in `DYNAMIC_COMBO_NODES`), bottom padding, `byteplus.api_key_saved` listener. |
| `web/js/byteplus_progress.js` | `ComfyUI.BytePlus.ProgressBar` | Listens to `progress`/`executed`; draws a bar + "Ns / Ms" in `onDrawForeground` (**Classic Canvas only**). |

Both match nodes with `node.comfyClass.startsWith("BytePlus")` and read `Comfy.VueNodes.Enabled` **once at load** (toggling the setting needs a page reload).

When you change a Python input name that JS keys on, grep `web/js` for it — the JS is keyed by widget **name** strings and will silently stop working.

## Imports

Only these two are public; everything else under `scripts/` logs `[ComfyUI Notice] ... internal module` and may break. **The path depends on the file's depth**: files are served at `/extensions/<pack>/<path under web/>`, and ours live in `web/js/`, so:

```js
import { app } from "../../../scripts/app.js";   // web/js/foo.js -> /extensions/<pack>/js/foo.js
import { api } from "../../../scripts/api.js";
```

(`../../scripts/…` is only right for files directly in `web/`.) Every `.js` under `web/` (recursive) is auto-loaded; load order across packs is not guaranteed.

## Showing/hiding widgets — what actually works

| Frontend | Classic Canvas | Nodes 2.0 (Vue) |
|---|---|---|
| ≥ visibility API (has a `hidden` **setter** on the widget prototype; newer than 1.52.7 — exact release unverified) | `widget.hidden = true` | `widget.hidden = true` (also hides in the side panel) |
| 1.52.7 (ComfyUI 0.37.x) | `widget.hidden = true` (plain flag, honoured by `isWidgetVisible`) | **ignored** — no reactive hide from extensions; disable instead (`widget.disabled` + `widget.options.disabled`) |

`toggleWidget()` in this repo implements exactly that (`hasVisibilityApi(widget)` detects the setter). After toggling on canvas, resize: `node.setSize([node.size[0], node.computeSize()[1]])` and `node.graph?.setDirtyCanvas(true, true)`.

React to value changes by chaining `widget.callback` — both the canvas and Vue nodes call it on user edits. Values set by code (workflow load, `widget.value = …`) do **not** fire it, so re-run the rules afterwards (`applyAllWidgetRules`).

**Never** `Object.defineProperty(widget, "value", …)`: `value` is a prototype accessor backed by the widget value store; an own property shadows it, so programmatic writes stop reaching the store and Nodes 2.0 shows stale values (verified live on 1.52.7). Also avoid splicing `node.widgets`, relying on `node.size` as the rendered size, and `onDrawForeground` drawing in Vue mode. Reading `app.rootGraph` before the graph exists logs "ComfyApp graph accessed before initialization" — use `node.graph` or check `app.isGraphReady`.

## V3 dynamic inputs are native

The frontend itself implements `COMFY_DYNAMICCOMBO_V3` (adds/removes the selected option's widgets named `<combo>.<child>`, restores previous values when switching back, resizes), `COMFY_AUTOGROW_V3` and `COMFY_MATCHTYPE_V3`. **Do not write JS to show/hide DynamicCombo children.** Custom JS is only for rules the schema can't express (e.g. "`size == Custom` shows width/height" on a plain combo).

Before touching `restoreDynamicComboWidgetValues`, reproduce the original bug (saved Seedance 2 / Seedream 5 workflow reloading with shifted `widgets_values`) on the current frontend — it may be obsolete.

## Hooks and order

Startup: `init` → (`addCustomNodeDefs` → `beforeRegisterNodeDef` per node type → `registerCustomNodes` → `beforeRegisterVueAppNodeDefs`) → `setup`. The initial workflow loads **after** `setup`.

Graph load: `beforeLoadGraph` → `beforeConfigureGraph` → configure (fires `nodeCreated` per node) → `loadedGraphNode` per node → `afterConfigureGraph` → `afterLoadGraph`.

- Per-node setup that must see saved values → `loadedGraphNode(node)` or `afterConfigureGraph`, not `nodeCreated` (values aren't applied yet). This repo instead wraps `node.configure` inside `nodeCreated` — works, but `onConfigure` receives a **shallow copy** now, so mutations to the data don't persist.
- Listen to websocket events in `setup()`.
- Persist extension data in `node.properties` or `graph.extra`, never on ad-hoc node fields.

## Progress

The Python side (`executor._update_node_progress`) updates `get_progress_state()`, which the frontend shows as a **built-in per-node progress bar in both renderers** via the `progress_state` event, and also sends a legacy `progress` event `{value, max, node}` that `byteplus_progress.js` draws on canvas. For status text under a node, prefer `PromptServer.instance.send_progress_text(text, node_id)` (Python) → `progress_text` event (rendered natively). A custom websocket event name must have a listener registered (`api.addEventListener(name, …)`) or the frontend logs "Unknown message type".

## Debugging

- Custom-node JS does not load in the frontend's Vite dev server. Loop: run ComfyUI with the pack → edit JS → hard-reload browser → DevTools console.
- Console signals: `Error loading extension`, `Error calling extension '<name>' method '<hook>'`, `[DEPRECATED]`, `[ComfyUI Notice]`.
- Test every change in **both** renderers: Settings → toggle "Nodes 2.0" (`Comfy.VueNodes.Enabled`), reload, and also reload a saved example workflow from `example_workflows/`.
