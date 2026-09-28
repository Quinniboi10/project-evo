// Requires Playwright and Chromium. Optional env: PLAYWRIGHT_MODULE, VIS_NETWORK_PATH.
// Runs against disposable fixtures and a local static server; no project database is used.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "../src");

function fixture(count = 80, islands = 4) {
    const graph = { islands: Array.from({ length: islands }, (_, id) => id), nodes: [], edges: [] };
    graph.nodes.push({ id: 1, name: "Baseline", score: 10, score_label: "10", level: 0, island_ids: graph.islands, inspiration_ids: null });
    for (let id = 2; id <= count + 1; id++) {
        const island = (id - 2) % islands;
        const parent = id <= islands + 1 ? 1 : id - islands;
        graph.nodes.push({ id, name: `Attempt ${id}`, score: 10 + id, score_label: String(10 + id), level: Math.floor((id - 2) / islands) + 1, island_ids: [island], inspiration_ids: id > islands + 1 ? [id - 1] : [] });
        graph.edges.push({ from: parent, to: id, label: "IMPROVE" });
    }
    return graph;
}

async function checkGraph(page) {
    const state = await page.evaluate(() => {
        const ids = visibleGraph.nodes.map(node => node.id).sort((a, b) => a - b);
        const actual = nodes.getIds().sort((a, b) => a - b);
        const rendered = [...network.body.nodeIndices].sort((a, b) => a - b);
        const expectedEdges = visibleGraph.edges.map(edge => JSON.stringify([edge.from, edge.to])).sort();
        const positions = network.getPositions();
        const expected = islandPositions();
        const misplaced = fullGraph.islands?.length ? ids.filter(id => positions[id]?.x !== expected.get(id)?.x || positions[id]?.y !== expected.get(id)?.y) : [];
        return { ids, actual, rendered, expectedEdges, actualEdges: edges.getIds().sort(), misplaced, updating: updatingGraph };
    });
    assert.deepEqual(state.actual, state.ids, "Dataset must contain only the current population/history");
    assert.deepEqual(state.rendered, state.ids, "Renderer must not retain removed nodes");
    assert.deepEqual(state.actualEdges, state.expectedEdges, "Connections must match visible nodes");
    assert.deepEqual(state.misplaced, [], "Nodes must retain lineage positions, never circular defaults");
    assert.equal(state.updating, false);
}

async function run() {
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url, "http://localhost").pathname;
        const file = pathname === "/" ? "dashboard/index.html" : pathname.slice(1);
        if (!file.startsWith("dashboard/") || file.includes("..")) { response.writeHead(404).end(); return; }
        const types = { ".html": "text/html", ".css": "text/css", ".ttf": "font/ttf" };
        fs.readFile(path.join(root, file), (error, data) => {
            response.writeHead(error ? 404 : 200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
            response.end(error ? "Not found" : data);
        });
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        let snapshot = fixture();
        let offline = false;
        await page.route("**/api/graph", route => offline ? route.fulfill({ status: 503, body: "Unavailable" }) : route.fulfill({ json: snapshot }));
        await page.route("**/api/row/*", route => {
            const id = Number(route.request().url().split("/").pop());
            const node = snapshot.nodes.find(node => node.id === id);
            return route.fulfill({ json: { ...node, uuid: `uuid-${id}`, task: "IMPROVE", model: "test-model" } });
        });
        if (process.env.VIS_NETWORK_PATH) {
            await page.route("https://unpkg.com/**", route => route.fulfill({ path: process.env.VIS_NETWORK_PATH, contentType: "text/javascript" }));
        }
        await page.goto(`http://127.0.0.1:${server.address().port}`);
        await page.waitForFunction(() => typeof network !== "undefined" && network !== null);
        await page.evaluate(() => document.fonts.ready);
        await checkGraph(page);

        // Island headings stay outside the canvas, including while panning past them.
        await page.emulateMedia({ reducedMotion: "reduce" });
        for (const width of [1600, 390]) {
            await page.setViewportSize({ width, height: 1000 });
            await page.waitForTimeout(250);
            await page.locator("#fit").click();
            await page.waitForTimeout(100);
            const fit = await page.evaluate(() => {
                const graph = document.getElementById("graph").getBoundingClientRect();
                const container = document.getElementById("graph-container").getBoundingClientRect();
                const baseline = visibleGraph.nodes.find(node => node.level === 0);
                const point = network.canvasToDOM(network.getPositions([baseline.id])[baseline.id]);
                return { gutter: graph.left - container.left, baselineX: point.x, width: graph.width };
            });
            assert.equal(fit.gutter, 112);
            assert.ok(fit.baselineX > 8 && fit.baselineX < fit.width, `Fit must keep the baseline visible beyond the fade: ${JSON.stringify(fit)}`);
            await page.evaluate(() => {
                const baseline = visibleGraph.nodes.find(node => node.level === 0);
                network.focus(baseline.id, { scale: 1, offset: { x: -document.getElementById("graph").clientWidth / 2 - 30, y: 0 }, animation: false });
                updateNodeLabels();
                drawBands();
            });
            const gutter = await page.evaluate(() => {
                const graph = document.getElementById("graph");
                const headings = [...document.querySelectorAll("#band-overlay text")];
                return {
                    aligned: headings.length > 0 && headings.every(label => label.getAttribute("x") === "24"),
                    outside: headings.every(label => label.getBoundingClientRect().right < graph.getBoundingClientRect().left),
                    clipped: getComputedStyle(graph).overflow === "hidden",
                    faded: getComputedStyle(graph).maskImage.includes("linear-gradient")
                };
            });
            assert.deepEqual(gutter, { aligned: true, outside: true, clipped: true, faded: true });
        }
        await page.setViewportSize({ width: 1600, height: 1000 });
        await page.locator("#fit").click();
        await page.screenshot({ path: "/tmp/evo-island-gutter.png" });
        await page.emulateMedia({ reducedMotion: "no-preference" });

        // Switching during selection and camera animation used to leave partial datasets.
        for (let i = 0; i < 30; i++) {
            await page.locator("#best").click();
            await page.locator(`.island-card[data-island="${i % 5 === 4 ? "all" : i % 4}"]`).click();
            await checkGraph(page);
        }
        // Exercise synchronous changes faster than a frame, with hover callbacks active.
        await page.evaluate(() => {
            for (let i = 0; i < 60; i++) {
                const id = visibleGraph.nodes.at(-1).id;
                hoveredNodeId = id;
                showPopup(id, { x: 100, y: 100 });
                document.querySelector(`.island-card[data-island="${i % 4}"]`).click();
            }
        });
        await checkGraph(page);
        assert.deepEqual(errors, []);

        // Observe every stage of a full replay, including a live snapshot arriving midway.
        await page.locator('.island-card[data-island="all"]').click();
        await page.locator("#replay").click();
        const steps = new Set();
        for (let i = 0; i < 110; i++) {
            await page.waitForTimeout(100);
            await checkGraph(page);
            steps.add(await page.locator("#step").inputValue());
            if (i === 30) {
                snapshot = fixture(84);
                await page.evaluate(() => refreshGraph());
                assert.equal(await page.evaluate(() => fullGraph.nodes.length), 81);
            }
        }
        assert.ok(steps.size > 25, "Replay must advance through multiple snapshots");
        assert.equal(await page.locator("#step").inputValue(), "84");
        assert.equal(await page.locator("#replay").textContent(), "Replay");
        await page.locator("#replay").click();
        await page.waitForTimeout(500);
        await page.locator("#replay").click();
        const paused = await page.locator("#step").inputValue();
        await page.waitForTimeout(200);
        assert.equal(await page.locator("#step").inputValue(), paused);
        for (const value of ["20", "0", "83", "4", "84"]) {
            await page.locator("#step").fill(value);
            await checkGraph(page);
        }
        await page.locator("#replay").click();
        await page.locator('.island-card[data-island="1"]').click();
        assert.equal(await page.evaluate(() => replayTimer), null);
        await page.locator("#latest").click();
        await checkGraph(page);

        // Selection survives refresh, but is cleared when a filter/history hides it.
        await page.locator("#best").click();
        const selected = await page.evaluate(() => selectedNodeId);
        await page.evaluate(() => refreshGraph());
        assert.equal(await page.evaluate(() => selectedNodeId), selected);
        await page.locator('.island-card[data-island="2"]').click();
        assert.equal(await page.evaluate(() => selectedNodeId), null);
        await checkGraph(page);

        for (const [width, height] of [[1600, 1000], [1280, 800], [1024, 768], [768, 1024], [390, 844], [320, 740]]) {
            await page.setViewportSize({ width, height });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            assert.ok(await page.locator("#graph").evaluate(element => element.getBoundingClientRect().height) > 200);
        }
        await page.locator("#inspect-toggle").click();
        assert.equal(await page.locator("#attempt-select").evaluate(element => element === document.activeElement), true);
        await page.locator("#attempt-select").selectOption(String(snapshot.nodes.find(node => node.island_ids.length === 1 && node.island_ids[0] === 2).id));
        await page.waitForFunction(() => document.getElementById("attempt-model").textContent === "test-model");
        await page.locator("#close-inspector").click();
        assert.equal(await page.locator("#inspector").isVisible(), false);

        await page.setViewportSize({ width: 1600, height: 1000 });
        await page.evaluate(() => { selectAttempt(null); selectedIsland = null; });
        for (const next of [fixture(0), { nodes: [], edges: [], islands: [] }, fixture(1000, 20), fixture()]) {
            snapshot = next;
            await page.evaluate(graph => { receiveGraph(graph); step.value = step.max; showAttempt(); }, snapshot);
            await checkGraph(page);
            if (snapshot.nodes.length > 1000) {
                await page.emulateMedia({ reducedMotion: "reduce" });
                await page.locator("#fit").click();
                await page.evaluate(() => { hoveredNodeId = null; updateNodeLabels(); });
                assert.equal(await page.evaluate(() => network.getScale() < 0.7), true);
                assert.equal(await page.evaluate(() => nodes.get().every(node => node.label === "")), true, "Overview must treat baseline and best labels like all other nodes");
                await page.locator("#best").click();
                assert.equal(await page.evaluate(() => nodes.get().every(node => node.label.length > 0)), true, "Detail zoom must reveal every node label");
                assert.equal(await page.evaluate(() => nodes.get(best.id).font.size * network.getScale()), 12, "Instant focusing must not magnify overview labels");
                await page.locator("#attempt-select").selectOption("2");
                assert.equal(await page.evaluate(() => nodes.get(2).font.size * network.getScale()), 12);
                await page.locator("#clear-selection").click();
            }
        }
        snapshot = fixture();
        snapshot.islands = [];
        snapshot.nodes.forEach(node => { node.island_ids = []; node.inspiration_ids = null; });
        await page.evaluate(graph => receiveGraph(graph), snapshot);
        assert.equal(await page.locator("#legacy-note").isVisible(), true);
        await checkGraph(page);
        snapshot = fixture();
        await page.evaluate(graph => receiveGraph(graph), snapshot);
        await checkGraph(page);
        await page.emulateMedia({ reducedMotion: "reduce" });
        assert.equal(await page.evaluate(() => animate()), false);
        offline = true;
        await page.evaluate(() => refreshGraph());
        assert.equal(await page.locator("#live-status").textContent(), "Reconnecting…");
        await checkGraph(page);
        assert.deepEqual(errors, []);
        console.log("Browser tests passed: rapid switching, renderer positions, full replay, live updates, selection, six viewports, empty/legacy/1,000-attempt data, and reconnecting");
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
