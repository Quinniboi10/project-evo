function deriveView(snapshot, activeAttempts, { island, step, replaying }) {
    const timelineNodes = snapshot.nodes.slice(0, step + 1);
    const nodes = timelineNodes.filter(node => island === null || node.island_ids?.includes(island));
    const liveNodes = step === Math.max(0, snapshot.nodes.length - 1) && !replaying
        ? activeAttempts.filter(node => island === null || node.island_ids.includes(island)) : [];
    nodes.push(...liveNodes);
    const visibleIds = new Set(nodes.map(node => node.id));
    const edges = [...snapshot.edges, ...liveNodes.map(node => ({ from: node.parent_id, to: node.id, label: node.task }))]
        .filter(edge => visibleIds.has(edge.from) && visibleIds.has(edge.to));
    const parents = new Map(edges.map(edge => [edge.to, edge.from]));
    const roots = nodes.filter(node => !parents.has(node.id));
    const baseline = roots.length === 1 ? roots[0] : null;
    const best = nodes.reduce((best, node) => Number.isFinite(node.score) && (!best || node.score > best.score) ? node : best, null);
    const ratio = best && baseline && baseline.score !== 0 ? best.score / baseline.score : NaN;
    const bestPath = new Set();
    let ancestor = best?.id;
    while (ancestor !== undefined && !bestPath.has(ancestor)) {
        bestPath.add(ancestor);
        ancestor = parents.get(ancestor);
    }
    const islands = [...(snapshot.islands ?? [])].sort((a, b) => a - b);
    const parentIds = new Set(snapshot.edges.map(edge => edge.to));
    const populations = [null, ...islands].map(island => {
        const members = timelineNodes.filter(node => island === null || node.island_ids?.includes(island));
        const champion = members.reduce((best, node) => !best || node.score > best.score ? node : best, null);
        return { island, champion, baseline: members.find(node => !parentIds.has(node.id)),
            attempts: members.filter(node => parentIds.has(node.id)).length };
    });
    return { timelineNodes, graph: { nodes, edges }, islands, populations, best, baseline, ratio, bestPath,
        snapshotBaseline: snapshot.nodes.find(node => !parentIds.has(node.id)),
        attempts: nodes.filter(node => !node.active).length - roots.length,
        parentsExplored: new Set(edges.filter(edge => typeof edge.to === "number").map(edge => edge.from)).size,
        positions: islands.length ? islandPositions(snapshot, nodes) : new Map() };
}

function islandPositions(snapshot, visibleNodes) {
    const positions = new Map();
    let top = 0;
    const layoutNodes = [...snapshot.nodes, ...visibleNodes.filter(node => node.active)];
    const parents = new Map(snapshot.edges.map(edge => [edge.to, edge.from]));
    for (const node of layoutNodes.filter(node => node.active)) parents.set(node.id, node.parent_id);
    // Use the complete snapshot so replay reveals nodes without reordering islands.
    for (const island of [...(snapshot.islands ?? [])].sort((a, b) => a - b)) {
        const levels = new Map();
        for (const node of layoutNodes) {
            if (node.level === 0 || node.island_ids?.length !== 1 || node.island_ids[0] !== island) continue;
            if (!levels.has(node.level)) levels.set(node.level, []);
            levels.get(node.level).push(node);
        }
        const rows = Math.max(1, ...[...levels.values()].map(layer => layer.length));
        for (const [level, layer] of [...levels].sort((a, b) => a[0] - b[0])) {
            // Keep families in parent order to avoid crossings between generations.
            layer.sort((a, b) => (positions.get(parents.get(a.id))?.y ?? 0) - (positions.get(parents.get(b.id))?.y ?? 0)
                || Number(Boolean(a.active)) - Number(Boolean(b.active)) || (a.active ? a.id.localeCompare(b.id) : a.id - b.id));
            layer.forEach((node, index) => positions.set(node.id, {
                x: level * 230, y: top + (rows - layer.length) * 50 + index * 100
            }));
        }
        top += rows * 100 + 100;
    }
    for (const node of layoutNodes) {
        if (!positions.has(node.id)) positions.set(node.id, { x: node.level * 230, y: Math.max(0, top - 200) / 2 });
    }
    return positions;
}

if (typeof module !== "undefined") module.exports = { deriveView, islandPositions };
