import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

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

function getGraph() {
    if ("rootGraphOrUndefined" in app) return app.rootGraphOrUndefined;
    if (app.isGraphReady === false) return undefined;
    return app.rootGraph ?? app.graph;
}

// Progress events are broadcast, so they can name nodes of any pack (or
// frontend-only nodes without a comfyClass) in whatever graph is open.
function isBytePlusNode(node) {
    return typeof node?.comfyClass === "string" && node.comfyClass.startsWith("BytePlus");
}

app.registerExtension({
    name: "ComfyUI.BytePlus.ProgressBar",

    async setup() {
        api.addEventListener("progress", ({ detail }) => {
            const { value, max, node } = detail;
            const graphNode = getGraph()?.getNodeById(node);
            
            if (isBytePlusNode(graphNode) && max > 0) {
                const ratio = value / max;
                graphNode.byteplus_progress_ratio = ratio;
                graphNode.byteplus_progress_text = `${value}s / ${max}s`;
                
                getGraph()?.setDirtyCanvas(true, false);
            }
        });

        api.addEventListener("executed", ({ detail }) => {
             const graphNode = getGraph()?.getNodeById(detail.node);
             if (isBytePlusNode(graphNode)) {
                 graphNode.byteplus_progress_ratio = 0;
                 graphNode.byteplus_progress_text = "";
                 graphNode.byteplus_progress_mode = null;
                 graphNode.byteplus_progress_startTime = 0;
                 graphNode.byteplus_progress_duration = 0;
                 getGraph()?.setDirtyCanvas(true, false);
             }
        });
    },

    nodeCreated(node) {
        if (isBytePlusNode(node)) {
            const origOnDrawForeground = node.onDrawForeground;

            node.onDrawForeground = function(ctx) {
                if (origOnDrawForeground) origOnDrawForeground.apply(this, arguments);
                // Canvas drawing only; Nodes 2.0 shows the built-in progress bar.
                // Checked per draw so toggling the renderer needs no reload.
                if (isVueNodesEnabled()) return;

                if (this.byteplus_progress_ratio > 0 && this.byteplus_progress_ratio < 1) {
                    const w = this.size[0];
                    // Bar height
                    const titleHeight = 0;
                    const barHeight = 4;
                    const barY = titleHeight;
                    
                    const textX = w - 10;
                    // Text position
                    const textY = -14;

                    ctx.save();

                    // Bar track
                    ctx.fillStyle = "rgba(0, 0, 0, 0.2)";
                    ctx.fillRect(0, barY, w, barHeight);

                    // Bar fill
                    ctx.fillStyle = "#4caf50"; 
                    ctx.beginPath();
                    if (this.byteplus_progress_ratio > 0.99) {
                        ctx.rect(0, barY, w * this.byteplus_progress_ratio, barHeight); 
                    } else {
                        ctx.roundRect(0, barY, w * this.byteplus_progress_ratio, barHeight, [0, 4, 4, 0]);
                    }
                    ctx.fill();
                    
                    // Label
                    if (this.byteplus_progress_text) {
                        ctx.fillStyle = "rgba(255, 255, 255, 0.6)";
                        ctx.font = "12px Arial";
                        ctx.textAlign = "right";
                        ctx.textBaseline = "middle";
                        ctx.fillText(this.byteplus_progress_text, textX, textY);
                    }
                    
                    ctx.restore();
                }
            };
        }
    }
});
