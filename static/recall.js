/* Representative recall routes use only positive, real synapses, within the server's two hops. */
const MemoryRecall = (() => {
  const edgeKey = (a, b) => [a, b].sort().join("|");

  function plan(results, synapses) {
    const direct = new Set(results.filter((item) => item.relevance > 0 || ["关键词匹配", "修正内容匹配"].includes(item.reason)).map((item) => item.id));
    const associated = results.filter((item) => !direct.has(item.id));
    const neighbours = new Map();
    for (const edge of synapses) {
      if (!(edge.w > 0) || edge.pruned) continue;
      for (const [from, to] of [[edge.a, edge.b], [edge.b, edge.a]]) {
        if (!neighbours.has(from)) neighbours.set(from, []);
        neighbours.get(from).push({to, w: edge.w});
      }
    }
    const candidates = new Map();
    const remember = (id, route, weight) => {
      if (weight > (candidates.get(id)?.weight || 0)) candidates.set(id, {route, weight});
    };
    for (const seed of results.filter((item) => direct.has(item.id))) {
      for (const first of neighbours.get(seed.id) || []) {
        const weight = (seed.relevance || .001) * first.w * .5;
        remember(first.to, [seed.id, first.to], weight);
        for (const second of neighbours.get(first.to) || []) {
          if (second.to === seed.id) continue;
          remember(second.to, [seed.id, first.to, second.to], weight * second.w * .5);
        }
      }
    }
    const arrivals = new Map([...direct].map((id) => [id, 0]));
    const edges = new Map();
    let shown = 0;
    // A small set of strongest representative paths remains readable after the recall settles.
    for (const item of associated.slice(0, 8)) {
      const candidate = candidates.get(item.id);
      if (!candidate) continue;
      shown++;
      candidate.route.forEach((id, hop) => {
        arrivals.set(id, Math.min(arrivals.get(id) ?? Infinity, hop));
        if (!hop) return;
        const from = candidate.route[hop - 1], key = edgeKey(from, id);
        if (!edges.has(key) || hop < edges.get(key).hop) edges.set(key, {from, to: id, hop});
      });
    }
    return {direct, associatedCount: associated.length, arrivals, edges, shown};
  }

  return {plan, edgeKey};
})();
if (typeof module !== "undefined" && module.exports) module.exports = MemoryRecall;
