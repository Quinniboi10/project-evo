// Run with node src/tests/test_dashboard.js. Tests state transitions without a browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
    constructor(tag = "div") {
        this.tag = tag;
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.style = { setProperty() {} };
        this.value = "0";
        this.max = "0";
        this.hidden = false;
        this.clientWidth = 1000;
        this.clientHeight = 600;
        this.offsetWidth = 200;
        this.offsetHeight = 100;
    }
    get firstChild() { return this.children[0]; }
    get lastChild() { return this.children.at(-1); }
    append(...elements) { for (const element of elements) { element.parent = this; this.children.push(element); } }
    replaceChildren(...elements) { this.children = []; this.append(...elements); }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener() {}
    focus() {}
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
    getBoundingClientRect() { return { left: 0, top: 0 }; }
}

class DataSet {
    constructor(values) { this.data = new Map(values.map(value => [value.id, value])); this.onChange = () => {}; }
    get(id) { return id === undefined ? [...this.data.values()] : this.data.get(id); }
    getIds() { return [...this.data.keys()]; }
    update(values) { for (const value of values) this.data.set(value.id, { ...this.data.get(value.id), ...value }); this.onChange(); }
    remove(ids) { for (const id of ids) this.data.delete(id); this.onChange(); }
}

class Network {
    constructor(_, data) { this.handlers = {}; this.data = data; this.position = { x: 42, y: 17 }; this.scale = 0.8; data.nodes.onChange = data.edges.onChange = () => this.redraw(); }
    on(event, handler) { this.handlers[event] = handler; }
    getViewPosition() { return { ...this.position }; }
    getScale() { return this.scale; }
    getPositions(ids = this.data.nodes.getIds()) { return Object.fromEntries(ids.filter(id => this.data.nodes.get(id)).map(id => [id, { x: id * 50, y: id * 20 }])); }
    canvasToDOM(point) { return point; }
    moveTo({ position, scale }) { this.position = position; this.scale = scale; }
    unselectAll() {}
    setSelection() {}
    setOptions() { this.redraw(); }
    redraw() { this.handlers.afterDrawing?.(); }
    fit() {}
    focus() {}
}

const elements = new Map();
const document = {
    fonts: { ready: Promise.resolve() },
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    },
    createElement: tag => new Element(tag),
    createElementNS: (_, tag) => new Element(tag),
    addEventListener() {}
};
const context = vm.createContext({
    document, getComputedStyle: () => ({ getPropertyValue: () => "#a5aaa7" }), window: { addEventListener() {}, matchMedia: () => ({ matches: true }) }, vis: { DataSet, Network },
    innerWidth: 1000, innerHeight: 700, setTimeout() {}, clearTimeout() {},
    setInterval() {}, clearInterval() {}, AbortSignal, AbortController,
    fetch: () => new Promise(() => {}), console, performance
});
const html = fs.readFileSync(`${__dirname}/../project_evo/dashboard/index.html`, "utf8");
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
const run = code => vm.runInContext(code, context);
const result = code => JSON.parse(JSON.stringify(run(code)));
run(`
    fullGraph = {
        islands: [0, 1],
        nodes: [
            { id: 1, level: 0, island_ids: [0, 1] },
            { id: 4, level: 2, island_ids: [0] },
            { id: 5, level: 2, island_ids: [0] },
            { id: 6, level: 2, island_ids: [0] },
            { id: 2, level: 1, island_ids: [0] },
            { id: 3, level: 1, island_ids: [0] },
            { id: 7, level: 1, island_ids: [1] }
        ],
        edges: [{ from: 1, to: 2 }, { from: 1, to: 3 }, { from: 3, to: 4 }, { from: 2, to: 5 }, { from: 2, to: 6 }, { from: 1, to: 7 }]
    };
    visibleGraph = { nodes: fullGraph.nodes, edges: fullGraph.edges };
`);
const familyOrder = "[...islandPositions()].filter(([id]) => [4, 5, 6].includes(id)).sort((a, b) => a[1].y - b[1].y).map(([id]) => id)";
assert.deepEqual(result(familyOrder), [5, 6, 4], "Children follow parent positions, with ID ordering among siblings");
const familyPositions = result("[...islandPositions()]");
assert.ok(run("islandPositions().get(7).y > Math.max(...[2, 3, 4, 5, 6].map(id => islandPositions().get(id).y))"));
run("visibleGraph.nodes = fullGraph.nodes.slice(0, 2)");
assert.deepEqual(result("[...islandPositions()]"), familyPositions, "Replay retains complete-snapshot positions");
run('visibleGraph.nodes = [{ id: "active:z", parent_id: 2, level: 2, island_ids: [0], active: true }, { id: "active:a", parent_id: 2, level: 2, island_ids: [0], active: true }]');
assert.deepEqual(result('[...islandPositions()].filter(([id]) => [4, 5, 6, "active:a", "active:z"].includes(id)).sort((a, b) => a[1].y - b[1].y).map(([id]) => id)'), [5, 6, "active:a", "active:z", 4]);
run("fullGraph = { nodes: [], edges: [] }; visibleGraph = { nodes: [], edges: [] }");
const graph = {
    islands: [0, 1, 2],
    nodes: [
        { id: 1, name: "Baseline", score: 10, score_label: "10", level: 0, island_ids: [0, 1, 2], inspiration_ids: null },
        { id: 2, name: "Foreign", score: 15, score_label: "15", level: 1, island_ids: [1], inspiration_ids: [] },
        { id: 3, name: "Local", score: 20, score_label: "20", level: 1, island_ids: [0], inspiration_ids: [2] }
    ],
    edges: [{ from: 1, to: 2 }, { from: 1, to: 3 }]
};
run(`receiveGraph(${JSON.stringify(graph)})`);
assert.deepEqual(result("nodes.getIds()"), [1, 2, 3]);
assert.equal(elements.get("island-overview").children.length, 4);
assert.match(elements.get("island-overview").children[3].lastChild.textContent, /Best 10/);
elements.get("island-overview").children[1].onclick();
assert.deepEqual(result("nodes.getIds()"), [1, 3]);
assert.equal(Number(run("step.value")), 2);
assert.equal(run("best.id"), 3);
const before = result("[nodes.get(), edges.get(), network.getViewPosition(), network.getScale()]");
run("showPopup(3, {x: 150, y: 100})");
assert.equal(elements.get("inspiration-overlay").children.filter(child => child.tag === "line").length, 1);
assert.equal(elements.get("inspiration-overlay").children.find(child => child.tag === "g").attributes["data-reference-id"], "2");
assert.deepEqual(result("[nodes.get(), edges.get(), network.getViewPosition(), network.getScale()]"), before);
run("selectedNodeId = 3; hidePopup()");
assert.equal(elements.get("inspiration-overlay").children.filter(child => child.tag === "line").length, 1);
assert.equal(elements.get("inspiration-overlay").children.find(child => child.tag === "g").children.filter(child => child.tag === "circle").length, 1);
run("showPopup(2, {x: 100, y: 40}); hidePopup()");
assert.equal(elements.get("inspiration-overlay").children.filter(child => child.tag === "line").length, 1);
run("selectedNodeId = null; updateSelection()");
assert.equal(elements.get("inspiration-overlay").children.length, 0);
run("step.value = 0; showAttempt()");
assert.deepEqual(result("nodes.getIds()"), [1]);
assert.equal(run("best.id"), 1);
assert.match(elements.get("island-overview").children[1].lastChild.textContent, /Best 10/);
run("selectedIsland = null; step.value = 2; showAttempt(); showPopup(3, {x: 150, y: 100})");
assert.equal(elements.get("inspiration-overlay").children.filter(child => child.tag === "g").length, 0);
assert.equal(elements.get("inspiration-overlay").children.filter(child => child.tag === "circle").length, 1);
const positionsBeforeReplay = result("[nodes.get(2).y, nodes.get(3).y]");
assert.ok(positionsBeforeReplay[1] < positionsBeforeReplay[0]);
run("step.value = 1; showAttempt(); step.value = 2; showAttempt(); showPopup(3, {x: 150, y: 100})");
assert.deepEqual(result("[nodes.get(2).y, nodes.get(3).y]"), positionsBeforeReplay);
assert.equal(elements.get("inspiration-overlay").children.filter(child => child.tag === "line").length, 1);
run("showPopup(1, {x: 50, y: 20})");
assert.equal(elements.get("popup-inspirations").textContent, "Inspiration history unavailable");
run("showPopup(2, {x: 100, y: 40})");
assert.equal(elements.get("popup-inspirations").textContent, "No inspiration references");
const updated = structuredClone(graph);
updated.nodes.push({ id: 4, name: "New", score: 30, score_label: "30", level: 2, island_ids: [0], inspiration_ids: [2] });
updated.edges.push({ from: 3, to: 4 });
run(`replayTimer = 1; receiveGraph(${JSON.stringify(updated)})`);
assert.equal(run("fullGraph.nodes.length"), 3);
run("stopReplay()");
assert.equal(run("fullGraph.nodes.length"), 4);
assert.equal(Number(run("step.value")), 3);
run("step.value = 1; showAttempt()");
run(`receiveGraph(${JSON.stringify(updated)})`);
assert.equal(Number(run("step.value")), 1);
assert.equal(run("network.getScale()"), 0.8);
const legacy = structuredClone(graph);
legacy.islands = [];
for (const node of legacy.nodes) node.island_ids = [];
run(`receiveGraph(${JSON.stringify(legacy)})`);
assert.equal(elements.get("island-overview").hidden, true);
console.log("Dashboard state tests passed: filters, replay, hover overlays, viewport, and legacy view");

async function testInspector() {
    const requests = [];
    context.fetch = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
    const flush = () => new Promise(resolve => setImmediate(resolve));
    run(`receiveGraph(${JSON.stringify(graph)}); step.value = step.max; showAttempt(); selectAttempt(3)`);
    assert.equal(elements.get("attempt-name").textContent, "Local");
    assert.equal(elements.get("attempt-parent").textContent, "#1");
    assert.equal(elements.get("attempt-ratio").textContent, "2.00× vs baseline");
    assert.equal(elements.get("attempt-inspirations").textContent, "#2");
    run("showPopup(2, {x: 10, y: 10})");
    assert.equal(elements.get("attempt-name").textContent, "Local");
    run("selectAttempt(2)");
    assert.equal(requests[0].options.signal.aborted, true);
    requests[0].resolve({ ok: true, json: async () => ({ id: 3, uuid: "stale", model: "stale" }) });
    await flush();
    assert.equal(elements.get("attempt-model").textContent, "—");
    requests[1].resolve({ ok: true, json: async () => ({ id: 2, uuid: "foreign-id", model: "test-model", task: "EXPLORE" }) });
    await flush();
    assert.equal(elements.get("attempt-model").textContent, "test-model");
    assert.equal(elements.get("attempt-task").textContent, "explore");
    assert.equal(elements.get("attempt-inspirations").textContent, "No inspiration references");
    assert.match(elements.get("row-values").textContent, /foreign-id/);
    run(`receiveGraph(${JSON.stringify(updated)})`);
    assert.equal(run("selectedNodeId"), 2);
    assert.equal(elements.get("attempt-model").textContent, "test-model");
    run("chooseIsland(0)");
    assert.equal(run("selectedNodeId"), null);
    assert.equal(elements.get("inspector-content").hidden, true);
    run("selectAttempt(3)");
    requests[2].resolve({ ok: false });
    await flush();
    assert.equal(elements.get("retry-row").hidden, false);
    assert.match(elements.get("row-status").textContent, /Could not load/);
    elements.get("retry-row").onclick();
    requests[3].resolve({ ok: true, json: async () => ({ id: 3, uuid: "local-id", model: null, task: "IMPROVE" }) });
    await flush();
    assert.equal(elements.get("retry-row").hidden, true);
    assert.equal(elements.get("attempt-model").textContent, "Not recorded");
    run("step.value = 0; showAttempt()");
    assert.equal(run("selectedNodeId"), null);
    elements.get("latest").onclick();
    assert.equal(Number(run("step.value")), Number(run("step.max")));
    assert.equal(elements.get("latest").disabled, true);
    run("network.scale = 0.3; updateNodeLabels()");
    assert.equal(run("nodes.get(3).label"), "");
    assert.equal(run("nodes.get(best.id).label"), "");
    assert.equal(run("nodes.get(1).label"), "");
    assert.equal(run("nodes.get(best.id).font.size * network.getScale()"), 12);
    run("hoveredNodeId = 3; updateNodeLabels()");
    assert.notEqual(run("nodes.get(3).label"), "");
    assert.equal(run("nodes.get(1).label"), "");
    run("hoveredNodeId = null; network.scale = 0.7; updateNodeLabels()");
    assert.equal(run("visibleGraph.nodes.every(node => nodes.get(node.id).label.length > 0)"), true);
    run("isLive = true; lastUpdated = Date.now(); updateLiveStatus()");
    assert.equal(elements.get("live-status").textContent, "Connected");
    run("isLive = false; updateLiveStatus()");
    assert.equal(elements.get("live-status").textContent, "Reconnecting…");
    run("selectAttempt(3); selectAttempt(null)");
    requests.at(-1).resolve({ ok: true, json: async () => ({ id: 3, uuid: "late", model: "late" }) });
    await flush();
    assert.equal(run("rowData"), null);
    console.log("Inspector tests passed: selection, stale requests, errors, retry, history, labels, and connection status");
}

testInspector().catch(error => { console.error(error); process.exitCode = 1; });
