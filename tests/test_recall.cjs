const {test} = require('node:test');
const assert = require('node:assert/strict');
const {plan, edgeKey} = require('../static/recall.js');
const hit = (id, relevance = 1) => ({id, relevance, reason: '关键词匹配'});
const background = id => ({id, relevance: 0, reason: '联想激活'});
const edge = (a, b, w = .8) => ({a, b, w});

test('background arrives along two real hops, including an unreturned intermediate', () => {
  const result = plan([hit('seed'), background('result')], [edge('seed', 'bridge'), edge('bridge', 'result')]);
  assert.deepEqual([...result.arrivals], [['seed', 0], ['bridge', 1], ['result', 2]]);
  assert.equal(result.edges.get(edgeKey('bridge', 'result')).from, 'bridge');
  assert.equal(result.associatedCount, 1);
});

test('never invents paths across disconnected, zero-weight, pruned or third-hop edges', () => {
  const results = [hit('seed'), ...['far', 'zero', 'pruned', 'isolated'].map(background)];
  const result = plan(results, [edge('seed', 'a'), edge('a', 'b'), edge('b', 'far'), edge('seed', 'zero', 0), {...edge('seed', 'pruned'), pruned: true}]);
  assert.equal(result.edges.size, 0);
  assert.equal(result.shown, 0);
  assert.equal(result.associatedCount, 4);
});

test('chooses a stronger two-hop contribution instead of a weak direct edge', () => {
  const result = plan([hit('seed'), background('result')], [edge('seed', 'bridge', .4), edge('bridge', 'result', .9), edge('seed', 'result', .1)]);
  assert.equal(result.arrivals.get('result'), 2);
  assert.equal(result.edges.has(edgeKey('seed', 'result')), false);
});

test('correction matches and rounded tiny keyword matches are direct hits', () => {
  const result = plan([{...hit('correction', 0), reason: '修正内容匹配'}, hit('tiny', 0)], []);
  assert.equal(result.direct.size, 2);
  assert.equal(result.associatedCount, 0);
});

test('limits visible paths while retaining the complete result count', () => {
  const ids = Array.from({length: 12}, (_, i) => `background-${i}`);
  const result = plan([hit('seed'), ...ids.map(background)], ids.map(id => edge('seed', id)));
  assert.equal(result.associatedCount, 12);
  assert.equal(result.shown, 8);
  assert.equal(result.edges.size, 8);
});

test('empty results produce no routes', () => {
  const result = plan([], [edge('a', 'b')]);
  assert.equal(result.direct.size, 0);
  assert.equal(result.arrivals.size, 0);
  assert.equal(result.edges.size, 0);
});
