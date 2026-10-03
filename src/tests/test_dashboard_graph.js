const assert = require("node:assert/strict");
const { deriveView, islandPositions } = require("../project_evo/dashboard/graph.js");

function freeze(value) {
    if (value && typeof value === "object") {
        Object.values(value).forEach(freeze);
        Object.freeze(value);
    }
    return value;
}

const snapshot = freeze({ islands: [1, 0], nodes: [
    { id: 1, level: 0, score: 10, island_ids: [0, 1] },
    { id: 2, level: 1, score: 12, island_ids: [0] },
    { id: 3, level: 1, score: 20, island_ids: [1] },
    { id: 4, level: 2, score: 15, island_ids: [0] }
], edges: [{ from: 1, to: 2 }, { from: 1, to: 3 }, { from: 2, to: 4 }] });
const active = freeze([{ id: "active:one", parent_id: 2, level: 2, task: "IMPROVE", active: true, score: null, island_ids: [0] }]);
const choices = freeze({ island: 0, step: 3, replaying: false });
const view = deriveView(snapshot, active, choices);
assert.deepEqual(view.graph.nodes.map(node => node.id), [1, 2, 4, "active:one"]);
assert.deepEqual(view.graph.edges.map(edge => edge.to), [2, 4, "active:one"]);
assert.equal(view.best.id, 4);
assert.equal(view.ratio, 1.5);
assert.equal(view.attempts, 2);
assert.equal(view.parentsExplored, 2);
assert.deepEqual([...view.bestPath], [4, 2, 1]);
assert.deepEqual(view.populations.map(row => [row.island, row.attempts, row.champion.id]), [[null, 3, 3], [0, 2, 4], [1, 1, 3]]);
const history = deriveView(snapshot, active, { ...choices, step: 1 });
assert.deepEqual(history.graph.nodes.map(node => node.id), [1, 2]);
assert.equal(history.populations[2].champion.id, 1);
const replay = deriveView(snapshot, active, { ...choices, replaying: true });
assert.deepEqual(replay.graph.nodes.map(node => node.id), [1, 2, 4]);
assert.deepEqual(history.positions, replay.positions, "Replay uses the complete saved layout");
assert.deepEqual(deriveView(snapshot, active, { ...choices, island: 1 }).graph.nodes.map(node => node.id), [1, 3]);
assert.equal(islandPositions(snapshot, []).get(3).y > islandPositions(snapshot, []).get(4).y, true);
const empty = deriveView(freeze({ nodes: [], edges: [] }), [], { ...choices, island: null, step: 0 });
assert.equal(empty.best, null);
assert.equal(empty.attempts, 0);
assert.equal(empty.positions.size, 0);
const baseline = deriveView(snapshot, active, { ...choices, step: 0 });
assert.equal(baseline.best.id, 1);
assert.equal(baseline.ratio, 1);
assert.equal(baseline.attempts, 0);
console.log("Dashboard calculation tests passed: filtering, layout, summaries, active attempts, and immutable inputs");
