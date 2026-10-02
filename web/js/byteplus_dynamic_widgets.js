import { app } from "../../../scripts/app.js";

function isVueNodesEnabled() {
    try {
        const setting = app?.extensionManager?.setting;
        if (typeof setting?.get === "function") {
            return setting.get("Comfy.VueNodes.Enabled") === true;
        }
        const settings = app?.ui?.settings;
        return settings?.getSettingValue?.("Comfy.VueNodes.Enabled", false) === true;
    } catch {
        return false;
    }
}

// Root graph without triggering the "accessed before initialization" error
// that app.rootGraph logs while the app is still starting.
function getRootGraph() {
    if ("rootGraphOrUndefined" in app) return app.rootGraphOrUndefined;
    if (app.isGraphReady === false) return undefined;
    return app.rootGraph ?? app.graph;
}

function markDirty(node) {
    const graph = node?.graph ?? getRootGraph();
    graph?.setDirtyCanvas?.(true, true);
}

// The renderer can be switched without a reload, so the setting is read
// whenever a rule is applied (and all rules re-run when it changes).
const VUE_NODES_SETTING = "Comfy.VueNodes.Enabled";

function isBytePlusNode(node) {
    return typeof node?.comfyClass === "string" && node.comfyClass.startsWith("BytePlus");
}

// Every node in the root graph and in subgraph definitions.
function allGraphNodes() {
    const root = getRootGraph();
    if (!root) return [];
    const nodes = [...(root.nodes ?? root._nodes ?? [])];
    const subgraphs = root.subgraphs;
    if (subgraphs && typeof subgraphs.values === "function") {
        for (const subgraph of subgraphs.values()) {
            nodes.push(...(subgraph?.nodes ?? subgraph?._nodes ?? []));
        }
    }
    return nodes;
}

/**
 * Widgets whose value changes drive visibility rules
 * @type {string[]}
 */
const TARGET_WIDGETS = ['reasoning_mode'];

/**
 * Extra space kept below the last widget
 * @type {number}
 */
const BOTTOM_PADDING = 8;

/**
 * Find a widget by name
 * @param {object} node - node instance
 * @param {string} name - widget name
 * @returns {object|null} the widget, or null
 */
function findWidgetByName(node, name) {
    if (!node.widgets) return null;
    return node.widgets.find((w) =>
        w.name === name || String(w.name || '').endsWith(`.${name}`)
    );
}

function getWidgetBaseName(widget) {
    return String(widget?.name || '').split('.').pop();
}

/**
 * Whether an input with this name exists and is linked
 * @param {object} node - node instance
 * @param {string} name - widget name
 * @returns {boolean} true if linked
 */
function isWidgetLinked(node, name) {
    return node.inputs ? node.inputs.some((input) => input.name === name && input.link != null) : false;
}

/**
 * Whether this frontend has the widget visibility API (a `hidden` setter that
 * hides the widget on the canvas, in Vue nodes and in the side panel).
 * @param {object} widget - widget
 * @returns {boolean}
 */
function hasVisibilityApi(widget) {
    let proto = Object.getPrototypeOf(widget);
    while (proto) {
        const descriptor = Object.getOwnPropertyDescriptor(proto, "hidden");
        if (descriptor) return typeof descriptor.set === "function";
        proto = Object.getPrototypeOf(proto);
    }
    return false;
}

function isHiddenWidget(widget) {
    return widget?.hidden === true || widget?.type === "hidden";
}

/**
 * Show or hide a widget. Uses `widget.hidden`, which the canvas honours on all
 * supported frontends. Vue nodes only honour it on frontends with the
 * visibility API; on older ones the widget is disabled instead.
 * @param {object} node - node instance
 * @param {object} widget - target widget
 * @param {boolean} show - whether to show it
 * @returns {boolean} whether visibility changed
 */
function toggleWidget(node, widget, show) {
    if (!widget) return false;

    // A widget whose input is linked is always shown (and re-shown when linked)
    if (isWidgetLinked(node, widget.name)) show = true;

    if (isVueNodesEnabled() && !hasVisibilityApi(widget)) {
        // Nodes 2.0 on frontends without a visibility API: disable instead of hide.
        let changed = setWidgetDisabled(widget, !show);
        if (widget.hidden === true) {
            widget.hidden = false;
            changed = true;
        }
        if (changed) markDirty(node);
        return changed;
    }

    // Clear a disable left over from Nodes 2.0 mode before hiding.
    let changed = setWidgetDisabled(widget, false);
    const hidden = !show;
    if ((widget.hidden === true) !== hidden) {
        widget.hidden = hidden;
        changed = true;
    }
    return changed;
}

// Only ever undo a disable this extension applied.
function setWidgetDisabled(widget, disabled) {
    if (!disabled && !widget._byteplusDisabled) return false;
    const changed = widget.disabled !== disabled || widget.options?.disabled !== disabled;
    widget.disabled = disabled;
    widget.options = widget.options || {};
    widget.options.disabled = disabled;
    if (widget.inputEl) widget.inputEl.disabled = disabled;
    widget._byteplusDisabled = disabled;
    return changed;
}

/**
 * Resize the node height, keeping any extra height the user added
 * @param {object} node - node instance
 * @param {number} [extraHeight=0] - extra height the user added manually
 */
function updateNodeHeight(node, extraHeight = 0) {
    if (isVueNodesEnabled()) return;
    if (node.flags?.collapsed) return;

    // Minimum required size
    const size = node.computeSize();

    // Add back the user's extra height
    const targetHeight = size[1] + extraHeight;

    node.setSize([node.size[0], targetHeight]);
    markDirty(node);
}

/**
 * Add bottom padding to the last visible widget
 * @param {object} node - node instance
 * @returns {boolean} whether anything changed
 */
function applyBottomPadding(node) {
    if (isVueNodesEnabled()) return false;
    if (!node.widgets) return false;

    // 1. Find the last visible widget
    let lastWidget = null;
    for (let i = node.widgets.length - 1; i >= 0; i--) {
        if (!isHiddenWidget(node.widgets[i])) {
            lastWidget = node.widgets[i];
            break;
        }
    }

    let changed = false;

    // 2. Remove padding added earlier to other widgets
    node.widgets.forEach(w => {
        if (w !== lastWidget && w.hasBottomPadding) {
            w.computeSize = w.origComputeSizeBeforePadding;
            delete w.origComputeSizeBeforePadding;
            delete w.hasBottomPadding;
            changed = true;
        }
    });

    if (!lastWidget) return changed;

    // 3. Pad the current last widget
    if (!lastWidget.hasBottomPadding) {
        if (!lastWidget.computeSize) {
            // No computeSize: use a default guess
            lastWidget.computeSize = () => [0, 20];
        }

        lastWidget.origComputeSizeBeforePadding = lastWidget.computeSize;
        const originalMethod = lastWidget.computeSize;

        lastWidget.computeSize = function (...args) {
            const size = originalMethod.apply(this, args);
            // Return a new array with the padding added to the height
            return [size ? size[0] : 0, (size ? size[1] : 20) + BOTTOM_PADDING];
        };

        lastWidget.hasBottomPadding = true;
        changed = true;
    }

    return changed;
}

// Nodes with a DynamicCombo input whose saved values need re-applying after load.
const DYNAMIC_COMBO_NODES = new Set([
    "BytePlusSeedream",
    "BytePlusSeedreamLayerSeparation",
    "BytePlusSeedance2TextToVideo",
    "BytePlusSeedance2FirstLastFrame",
    "BytePlusSeedance2Reference",
    "BytePlusSeed",
    "BytePlusSeedAudio",
    "BytePlusVideoEnhance",
    "BytePlusVideoSmoothness",
    "BytePlusImageEnhance",
]);
const SEED_CONTROL_VALUES = ["fixed", "increment", "decrement", "randomize"];

// Saved widgets_values keep a control_after_generate value after each seed,
// but DynamicCombo children get no control widget, so the frontend assigns
// every later value one slot too early. Re-apply the values in widget order,
// skipping the control slot.
function restoreDynamicComboWidgetValues(node, values) {
    const widgets = node.widgets || [];
    let index = 0;
    for (let w = 0; w < widgets.length && index < values.length; w++) {
        const widget = widgets[w];
        if (widget.value !== values[index]) widget.value = values[index];
        index++;
        // The control widget may carry a suffix (frontend 1.53 names it "control_after_generate#1").
        const nextIsControl = getWidgetBaseName(widgets[w + 1]).startsWith('control_after_generate');
        if (getWidgetBaseName(widget) === 'seed' && !nextIsControl &&
            SEED_CONTROL_VALUES.includes(values[index])) {
            index++;
        }
    }
}

// DynamicCombo children are created after configure: restore their values,
// re-install watchers once they exist, and keep the saved node size.
function refreshAfterConfigure(node, data) {
    const values = data?.widgets_values;
    const restoreValues = () => {
        if (DYNAMIC_COMBO_NODES.has(node.comfyClass) && Array.isArray(values) && values.length > 1) {
            restoreDynamicComboWidgetValues(node, values);
        }
    };
    queueMicrotask(restoreValues);
    setTimeout(() => {
        restoreValues();
        installWidgetWatchers(node);
        applyAllWidgetRules(node);
    }, 0);
    setTimeout(() => {
        restoreValues();
        installWidgetWatchers(node);
        applyAllWidgetRules(node);
        if (Array.isArray(data?.size) && data.size.length >= 2) {
            node.setSize?.([data.size[0], data.size[1]]);
        }
    }, 120);
}

/**
 * Apply widget visibility rules
 * @param {object} node - node instance
 * @param {object} widget - widget that changed
 */
function widgetLogic(node, widget) {
    const widgetName = getWidgetBaseName(widget);
    // Measure the extra height the user added before the layout changes, so it
    // can be restored after resizing.
    let extraHeight = 0;
    if (!isVueNodesEnabled() && node.size && node.computeSize && !node.flags?.collapsed) {
        extraHeight = Math.max(0, node.size[1] - node.computeSize()[1]);
    }

    let shouldResize = false;

    // BytePlus LLM: effort only applies while thinking is on
    if (node.comfyClass === "BytePlusSeed" && widgetName === 'reasoning_mode') {
        const isThinkingEnabled = widget.value !== "disabled";
        const effortWidget = findWidgetByName(node, 'reasoning_effort');
        if (toggleWidget(node, effortWidget, isThinkingEnabled)) shouldResize = true;
    }

    const paddingChanged = applyBottomPadding(node);

    // Skip resizing while loading a workflow so the saved size is kept
    if (node._isConfiguring) return;

    if (shouldResize || paddingChanged) {
        updateNodeHeight(node, extraHeight);
    }
}

function getWatchedWidgets(node) {
    return (node.widgets || []).filter(w => TARGET_WIDGETS.includes(getWidgetBaseName(w)));
}

/**
 * Re-evaluate every visibility rule, e.g. after values were set by code
 * (workflow load, DynamicCombo restore), which does not fire widget callbacks.
 */
function applyAllWidgetRules(node) {
    getWatchedWidgets(node).forEach(w => widgetLogic(node, w));
}

/**
 * Re-run the visibility rules when a watched widget changes. Chains
 * widget.callback, which both the canvas and Vue nodes call on user edits.
 * (Redefining widget.value would bypass the frontend's widget value store.)
 */
function installWidgetWatchers(node) {
    getWatchedWidgets(node).forEach(w => {
        if (w._byteplusWatcherInstalled) return;
        w._byteplusWatcherInstalled = true;
        widgetLogic(node, w);

        const originalCallback = w.callback;
        w.callback = function (...args) {
            const result = originalCallback?.apply(this, args);
            widgetLogic(node, w);
            return result;
        };
    });
}

// Re-apply every rule after the renderer is switched, so widgets disabled in
// Nodes 2.0 (or hidden on the canvas) get the other mode's treatment.
function onRendererChanged() {
    setTimeout(() => {
        for (const node of allGraphNodes()) {
            if (isBytePlusNode(node)) applyAllWidgetRules(node);
        }
    }, 150);
}

app.registerExtension({
    name: "ComfyUI.BytePlus.DynamicWidgets",

    async setup() {
        app.ui?.settings?.addEventListener?.(`${VUE_NODES_SETTING}.change`, onRendererChanged);
    },

    nodeCreated(node) {
        if (!isBytePlusNode(node)) return;

        // Wrap configure to know when a workflow is being loaded
        const origConfigure = node.configure;
        node.configure = function (data) {
            this._isConfiguring = true;
            const r = origConfigure ? origConfigure.apply(this, arguments) : undefined;
            refreshAfterConfigure(this, data);
            delete this._isConfiguring;
            return r;
        };

        // A widget converted to a linked input is never hidden, so re-evaluate
        // the rules when links change.
        const onConnectionsChange = node.onConnectionsChange;
        node.onConnectionsChange = function () {
            const r = onConnectionsChange ? onConnectionsChange.apply(this, arguments) : undefined;
            if (!this._isConfiguring) applyAllWidgetRules(this);
            return r;
        };

        installWidgetWatchers(node);
        setTimeout(() => installWidgetWatchers(node), 0);
        setTimeout(() => installWidgetWatchers(node), 120);
    }
});
