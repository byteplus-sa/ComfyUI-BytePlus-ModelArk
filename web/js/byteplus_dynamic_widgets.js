import { app } from "/scripts/app.js";

function isVueNodesEnabled() {
    const settings = app?.ui?.settings;
    const getter = settings?.getSettingValue;
    if (typeof getter !== "function") return false;
    try {
        return getter.call(settings, "Comfy.VueNodes.Enabled", false) === true;
    } catch {
        return false;
    }
}

const VUE_NODES_ENABLED = isVueNodesEnabled();

/**
 * Widgets whose value changes drive visibility logic
 * @type {string[]}
 */
const TARGET_WIDGETS = [
    'model_version',
    'size',
    'enable_group_generation',
    'generation_count',
    'enable_timeout_setting',
    'enable_random_seed',
    'auto_duration',
    'draft_mode',
    'reuse_last_draft_task',
    'key_name',
    'reasoning_mode',
    'prompt_optimization'
];

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
 * Show or hide a widget
 * @param {object} node - node instance
 * @param {object} widget - target widget
 * @param {boolean} show - whether to show it
 * @returns {boolean} whether visibility changed
 */
function toggleWidget(node, widget, show) {
    if (!widget) return false;

    if (VUE_NODES_ENABLED) {
        const disabled = !show;
        const changed = widget.disabled !== disabled || widget.options?.disabled !== disabled;
        widget.disabled = disabled;
        widget.options = widget.options || {};
        widget.options.disabled = disabled;
        if (widget.inputEl) widget.inputEl.disabled = disabled;
        if (changed) app.graph.setDirtyCanvas(true, true);
        return changed;
    }

    // Never hide a widget that was converted to a linked input
    if (isWidgetLinked(node, widget.name)) return false;

    // Remember the original type and size function
    if (!widget.origType && widget.type !== "hidden") {
        widget.origType = widget.type;
        widget.origComputeSize = widget.computeSize;
    }

    // Hidden without a cached original state: cannot restore, treat as unchanged
    if (!widget.origType && widget.type === "hidden") {
        return false;
    }

    // Nothing to do if already in the target state
    const isCurrentlyHidden = widget.type === "hidden";
    if (show !== isCurrentlyHidden) return false;

    // Apply the change
    if (show) {
        widget.type = widget.origType;
        widget.computeSize = widget.origComputeSize;
    } else {
        widget.type = "hidden";
        widget.computeSize = () => [0, -4];
    }

    return true;
}

/**
 * Resize the node height, keeping any extra height the user added
 * @param {object} node - node instance
 * @param {number} [extraHeight=0] - extra height the user added manually
 */
function updateNodeHeight(node, extraHeight = 0) {
    if (VUE_NODES_ENABLED) return;
    if (node.flags?.collapsed) return;

    // Minimum required size
    const size = node.computeSize();

    // Add back the user's extra height
    const targetHeight = size[1] + extraHeight;

    node.setSize([node.size[0], targetHeight]);
    app.graph.setDirtyCanvas(true, true);
}

/**
 * Add bottom padding to the last visible widget
 * @param {object} node - node instance
 * @returns {boolean} whether anything changed
 */
function applyBottomPadding(node) {
    if (VUE_NODES_ENABLED) return false;
    if (!node.widgets) return false;

    // 1. Find the last visible widget
    let lastWidget = null;
    for (let i = node.widgets.length - 1; i >= 0; i--) {
        if (node.widgets[i].type !== "hidden") {
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

const AUTOGROW_LABEL_RULES = {
    BytePlusSeedream4: [
        { prefix: "image_", label: "Image" },
    ],
    BytePlusSeedream5: [
        { prefix: "image_", label: "Image" },
    ],
    BytePlusSeedance2: [
        { prefix: "ref_image_", label: "Ref Image" },
        { prefix: "ref_video_", label: "Ref Video" },
        { prefix: "ref_audio_", label: "Ref Audio" },
    ],
};

function getAutogrowLabelRules(node) {
    if (!node || !node.comfyClass) return [];
    return AUTOGROW_LABEL_RULES[node.comfyClass] || [];
}

function escapeRegex(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function setInputDisplayText(input, text) {
    const changed =
        input.label !== text ||
        input.localized_name !== text ||
        input.display_name !== text;
    input.label = text;
    input.localized_name = text;
    input.display_name = text;
    return changed;
}

function applyAutogrowInputLabels(node) {
    if (!node.inputs || node.inputs.length === 0) return false;
    const rules = getAutogrowLabelRules(node);
    if (!rules || rules.length === 0) return false;

    let changed = false;

    for (const input of node.inputs) {
        if (!input || !input.name) continue;
        const inputName = String(input.name);
        for (const rule of rules) {
            // Match both plain and namespaced Autogrow input names
            const pattern = new RegExp(`(?:^|\\.)${escapeRegex(rule.prefix)}(\\d+)$`);
            const matched = inputName.match(pattern);
            if (!matched) continue;
            const suffixNum = parseInt(matched[1], 10);
            if (!Number.isFinite(suffixNum)) continue;
            const expectedLabel = `${rule.label} ${suffixNum}`;
            if (setInputDisplayText(input, expectedLabel)) {
                changed = true;
            }
            break;
        }
    }
    return changed;
}

function refreshAutogrowInputLabels(node) {
    if (applyAutogrowInputLabels(node)) {
        app.graph.setDirtyCanvas(true, true);
    }
}

const DYNAMIC_COMBO_NODES = new Set(["BytePlusSeedance2", "BytePlusSeedream5"]);
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
        const nextIsControl = getWidgetBaseName(widgets[w + 1]) === 'control_after_generate';
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
    }, 0);
    setTimeout(() => {
        restoreValues();
        installWidgetWatchers(node);
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
    // 1. Measure how much extra height the user added before the layout changes
    // so it can be restored after resizing
    let extraHeight = 0;
    if (!VUE_NODES_ENABLED && node.size && node.computeSize && !node.flags?.collapsed) {
        const currentMinHeight = node.computeSize()[1];
        const currentActualHeight = node.size[1];
        // Clamp negative values
        extraHeight = Math.max(0, currentActualHeight - currentMinHeight);
    }

    let shouldResize = false;

    // Image nodes
    if (node.comfyClass === "BytePlusSeedream4" || node.comfyClass === "BytePlusSeedream5") {
        if (widgetName === 'size') {
            const isCustom = widget.value === "Custom";
            const widthWidget = findWidgetByName(node, 'width');
            const heightWidget = findWidgetByName(node, 'height');

            const changedW = toggleWidget(node, widthWidget, isCustom);
            const changedH = toggleWidget(node, heightWidget, isCustom);

            if (changedW || changedH) shouldResize = true;
        }

        if (node.comfyClass === "BytePlusSeedream4" && widgetName === 'model_version') {
            const optimizePromptWidget = findWidgetByName(node, 'prompt_optimization');
            const isSupported = widget.value === "seedream-4-0";
            if (toggleWidget(node, optimizePromptWidget, isSupported)) shouldResize = true;
        }
    }

    // Group generation
    if (node.comfyClass === "BytePlusSeedream4" || node.comfyClass === "BytePlusSeedream5") {
        if (widgetName === 'enable_group_generation') {
            const isGroupMode = widget.value === true;
            const maxImagesWidget = findWidgetByName(node, 'max_images');

            if (toggleWidget(node, maxImagesWidget, isGroupMode)) shouldResize = true;
        }
    }

    // Video nodes
    if (node.comfyClass === "BytePlusSeedance1" ||
        node.comfyClass === "BytePlusSeedance1_5" ||
        node.comfyClass === "BytePlusSeedance2") {

        // 1.5 / 2.x: auto duration hides duration
        if (node.comfyClass === "BytePlusSeedance1_5" || node.comfyClass === "BytePlusSeedance2") {
            if (widgetName === 'auto_duration') {
                const isAuto = widget.value === true;
                const durationWidget = findWidgetByName(node, 'duration');
                if (toggleWidget(node, durationWidget, !isAuto)) shouldResize = true;
            }
        }

        // 1.5 / 2.5 family: draft mode controls
        if (node.comfyClass === "BytePlusSeedance1_5" || node.comfyClass === "BytePlusSeedance2") {
            if (widgetName === 'draft_mode') {
                const isDraftMode = widget.value === true;
                const draftTaskWidget = findWidgetByName(node, 'draft_task_id');
                const reuseWidget = findWidgetByName(node, 'reuse_last_draft_task');


                if (toggleWidget(node, reuseWidget, isDraftMode)) shouldResize = true;

                if (isDraftMode) {
                    if (reuseWidget) {
                        widgetLogic(node, reuseWidget);
                    } else {
                        if (toggleWidget(node, draftTaskWidget, true)) shouldResize = true;
                    }
                } else {
                    if (toggleWidget(node, draftTaskWidget, false)) shouldResize = true;
                }
            }

            if (widgetName === 'reuse_last_draft_task') {
                const isReuse = widget.value === true;
                const draftTaskWidget = findWidgetByName(node, 'draft_task_id');
                const draftModeWidget = findWidgetByName(node, 'draft_mode');
                const isDraftMode = draftModeWidget ? draftModeWidget.value === true : false;

                if (isDraftMode) {
                    if (toggleWidget(node, draftTaskWidget, !isReuse)) shouldResize = true;
                } else {
                    if (toggleWidget(node, draftTaskWidget, false)) shouldResize = true;
                }
            }
        }

        // Batch options shown only for generation_count > 1
        if (widgetName === 'generation_count') {
            const isBatch = widget.value > 1;
            const batchPathWidget = findWidgetByName(node, 'filename_prefix');
            const saveLastFrameWidget = findWidgetByName(node, 'save_last_frame_batch');

            const changedPath = toggleWidget(node, batchPathWidget, isBatch);
            const changedSave = toggleWidget(node, saveLastFrameWidget, isBatch);

            if (changedPath || changedSave) shouldResize = true;
        }

        // Random seed hides the seed controls
        if (widgetName === 'enable_random_seed') {
            const useRandom = widget.value === true;
            const showSeedControls = !useRandom;

            const seedWidget = findWidgetByName(node, 'seed');
            const controlWidget = findWidgetByName(node, 'control_after_generate');

            const changedSeed = toggleWidget(node, seedWidget, showSeedControls);
            const changedControl = toggleWidget(node, controlWidget, showSeedControls);

            if (changedSeed || changedControl) shouldResize = true;
        }
    }

    // API Client node
    if (node.comfyClass === "BytePlusAPIClient") {
        if (widgetName === 'key_name') {
            const isCustom = widget.value === "Custom";
            const newKeyWidget = findWidgetByName(node, 'new_api_key');
            const newNameWidget = findWidgetByName(node, 'new_key_name');

            const changedKey = toggleWidget(node, newKeyWidget, isCustom);
            const changedName = toggleWidget(node, newNameWidget, isCustom);

            if (changedKey || changedName) shouldResize = true;
        }
    }

    // Visual understanding node
    if (node.comfyClass === "BytePlusVisualUnderstanding") {
        if (widgetName === 'reasoning_mode') {
            const isThinkingEnabled = widget.value !== "disabled";
            const effortWidget = findWidgetByName(node, 'reasoning_effort');
            
            if (toggleWidget(node, effortWidget, isThinkingEnabled)) shouldResize = true;
        }
    }

    // 2. If the layout changed, resize and restore the user's extra height
    const paddingChanged = applyBottomPadding(node);

    // Skip resizing while loading a workflow so the saved size is kept
    if (node._isConfiguring) return;

    if (shouldResize || paddingChanged) {
        updateNodeHeight(node, extraHeight);
    }
}

function installWidgetWatchers(node) {
    const widgetsToWatch = node.widgets?.filter(w =>
        TARGET_WIDGETS.includes(getWidgetBaseName(w))
    );
    if (!widgetsToWatch || widgetsToWatch.length === 0) return;

    widgetsToWatch.forEach(w => {
        if (w._byteplusValueWatcherInstalled) return;
        w._byteplusValueWatcherInstalled = true;
        widgetLogic(node, w);

        if (getWidgetBaseName(w) === 'model_version') {
            const originalCallback = w.callback;
            w.callback = function (...args) {
                const result = originalCallback?.apply(this, args);
                widgetLogic(node, w);
                queueMicrotask(() => installWidgetWatchers(node));
                setTimeout(() => installWidgetWatchers(node), 0);
                setTimeout(() => installWidgetWatchers(node), 120);
                return result;
            };
            return;
        }

        let widgetValue = w.value;
        try {
            Object.defineProperty(w, 'value', {
                configurable: true,
                enumerable: true,
                get() {
                    return widgetValue;
                },
                set(newVal) {
                    if (newVal !== widgetValue) {
                        widgetValue = newVal;
                        widgetLogic(node, w);
                    }
                }
            });
        } catch {
            delete w._byteplusValueWatcherInstalled;
        }
    });
}

app.registerExtension({
    name: "ComfyUI.BytePlus.DynamicWidgets",

    async setup() {
        const mode = VUE_NODES_ENABLED ? "Node2.0(Vue)" : "Legacy(Canvas)";
        console.log(`%c[BytePlus] Dynamic Widgets Extension Loaded (${mode})`, "color:green; font-weight:bold;");
    },

    nodeCreated(node) {
        if (!node.comfyClass || !node.comfyClass.startsWith("BytePlus")) return;

        // Wrap configure to know when a workflow is being loaded
        const origConfigure = node.configure;
        node.configure = function (data) {
            this._isConfiguring = true;
            const r = origConfigure ? origConfigure.apply(this, arguments) : undefined;
            refreshAfterConfigure(this, data);
            delete this._isConfiguring;
            return r;
        };

        const onConnectionsChange = node.onConnectionsChange;
        node.onConnectionsChange = function (type, index, connected, link_info, slot) {
            const r = onConnectionsChange ? onConnectionsChange.apply(this, arguments) : undefined;
            if (!this._isConfiguring) {
                refreshAutogrowInputLabels(this);
            }
            return r;
        };

        refreshAutogrowInputLabels(node);
        setTimeout(() => refreshAutogrowInputLabels(node), 0);
        setTimeout(() => refreshAutogrowInputLabels(node), 120);
        setTimeout(() => refreshAutogrowInputLabels(node), 360);

        installWidgetWatchers(node);
        setTimeout(() => installWidgetWatchers(node), 0);
        setTimeout(() => installWidgetWatchers(node), 120);
    }
});
