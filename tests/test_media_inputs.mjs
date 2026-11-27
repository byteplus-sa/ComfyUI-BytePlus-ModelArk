import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../web/js/byteplus_media_inputs.js", import.meta.url));
const { MEDIA_INPUTS, migrateMediaInputs } = await import(`data:text/javascript;base64,${source.toString("base64")}`);

test("old media names migrate without shifting widget values or links", () => {
    for (const [type, [combo, names]] of Object.entries(MEDIA_INPUTS)) {
        const values = ["model or mode", 42, "fixed"];
        const graph = {
            nodes: [{ id: 2, type, widgets_values: [...values],
                inputs: names.map((name, i) => ({ name: `${combo}.${name}`, link: i + 1 })) }],
            links: names.map((name, i) => [i + 1, 1, 0, 2, i, "IMAGE"]),
        };
        const links = structuredClone(graph.links);
        migrateMediaInputs(graph);
        assert.deepEqual(graph.nodes[0].inputs.map(i => i.name), names);
        assert.deepEqual(graph.nodes[0].widgets_values, values);
        assert.deepEqual(graph.links, links);
        const once = structuredClone(graph);
        migrateMediaInputs(graph);
        assert.deepEqual(graph, once);
    }
});

test("duplicate unconnected ports collapse and target slots are remapped", () => {
    const graph = {
        nodes: [{ id: 2, type: "BytePlusSeed", inputs: [
            { name: "images.image_1", link: null },
            { name: "model.images.image_1", link: 1 },
            { name: "model.videos.video_1", link: 2 },
        ] }],
        links: [[1, 1, 0, 2, 1, "IMAGE"], [2, 3, 0, 2, 2, "VIDEO"]],
    };
    migrateMediaInputs(graph);
    assert.deepEqual(graph.nodes[0].inputs, [
        { name: "images.image_1", link: 1 }, { name: "videos.video_1", link: 2 },
    ]);
    assert.equal(graph.links[0][4], 0);
    assert.equal(graph.links[1][4], 1);
});

test("conflicting connected sources are never silently lost", () => {
    const graph = { nodes: [{ type: "BytePlusSeedreamLayerSeparation", inputs: [
        { name: "image", link: 1 }, { name: "model.image", link: 2 },
    ] }] };
    assert.throws(() => migrateMediaInputs(graph), /two connected sources/);
});

test("subgraphs and object-form links migrate; unrelated controls do not", () => {
    const subgraph = {
        nodes: [{ id: "2", type: "BytePlusSeed", inputs: [
            { name: "images.image_1", link: null }, { name: "model.images.image_1", link: 1 },
            { name: "model.temperature", link: null },
        ] }],
        links: [{ id: 1, target_id: 2, target_slot: 1 }],
    };
    migrateMediaInputs({ definitions: { subgraphs: [subgraph] } });
    assert.equal(subgraph.links[0].target_slot, 0);
    assert.equal(subgraph.nodes[0].inputs[1].name, "model.temperature");
});
