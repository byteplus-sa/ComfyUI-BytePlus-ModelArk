// Media ports moved out of DynamicCombo groups. Keep saved link indices intact.
export const MEDIA_INPUTS = {
    BytePlusSeedAudio15: ["mode", ["audio", "video", "reference_image",
        ...Array.from({ length: 6 }, (_, i) => `reference_audio_${i + 1}`)]],
    BytePlusSeedAudio: ["reference_mode", ["reference_image",
        ...Array.from({ length: 3 }, (_, i) => `reference_audio_${i + 1}`)]],
    BytePlusSeed: ["model", ["images", "videos", "audios"]],
    BytePlusSeedream: ["model", ["images", "reference_mask"]],
    BytePlusSeedreamLayerSeparation: ["model", ["image"]],
    BytePlusSeedance2Reference: ["model", ["reference_images", "reference_videos", "reference_audios"]],
};

export function migrateMediaInputs(graph) {
    for (const node of graph?.nodes ?? []) {
        const rule = MEDIA_INPUTS[node.type];
        if (!rule || !Array.isArray(node.inputs)) continue;
        const [combo, names] = rule;
        const slots = new Map();
        const remap = [];
        const inputs = [];
        for (const [index, input] of node.inputs.entries()) {
            const prefix = `${combo}.`;
            const child = input.name.startsWith(prefix) ? input.name.slice(prefix.length) : null;
            const name = child && names.includes(child.split(".")[0]) ? child : input.name;
            const previous = slots.get(name);
            if (previous !== undefined) {
                const kept = inputs[previous];
                if (kept.link != null && input.link != null && kept.link !== input.link) {
                    throw new Error(`BytePlus: ${name} has two connected sources. Disconnect one before upgrading this workflow.`);
                }
                if (input.link != null) inputs[previous] = { ...input, name };
                remap[index] = previous;
            } else {
                remap[index] = inputs.length;
                slots.set(name, inputs.length);
                inputs.push({ ...input, name });
            }
        }
        for (const link of graph.links ?? []) {
            if (Array.isArray(link)) {
                if (String(link[3]) === String(node.id)) link[4] = remap[link[4]] ?? link[4];
            } else if (String(link.target_id) === String(node.id)) {
                link.target_slot = remap[link.target_slot] ?? link.target_slot;
            }
        }
        node.inputs = inputs;
    }
    for (const subgraph of graph?.definitions?.subgraphs ?? []) migrateMediaInputs(subgraph);
}
