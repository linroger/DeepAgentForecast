import assert from 'node:assert/strict'
import test from 'node:test'

import {
  boundedFocusNodeIds,
  graphPayloadRevision,
  graphRequestParams,
} from '../src/utils/graphPayload.js'

test('initial graph request is a slim configured overview', () => {
  assert.deepEqual(graphRequestParams(), { top_k: 0, slim: 'true' })
  assert.deepEqual(graphRequestParams({ full: true }), { full: 'true' })
})

test('focus selection stays bounded and retains center and researched actors', () => {
  const candidateNodes = Array.from({ length: 1000 }, (_, i) => ({ uuid: `n${i}` }))
  const edges = Array.from({ length: 999 }, (_, i) => ({
    uuid: `e${i}`,
    source_node_uuid: `n${i}`,
    target_node_uuid: `n${i + 1}`,
  }))
  const kept = boundedFocusNodeIds({
    candidateNodes,
    edges,
    baseNodeIds: candidateNodes.slice(0, 400).map(node => node.uuid),
    expandedNodeIds: candidateNodes.slice(400).map(node => node.uuid),
    seedNodeIds: ['n998'],
    activeNodeId: 'n999',
    limit: 400,
  })
  assert.equal(kept.size, 400)
  assert.ok(kept.has('n999'))
  assert.ok(kept.has('n998'))
})

test('overview revision ignores ordering but detects structural changes', () => {
  const payload = {
    graph_id: 'g',
    total_node_count: 2,
    nodes: [{ uuid: 'a', name: 'A' }, { uuid: 'b', name: 'B' }],
    edges: [{ uuid: 'e', source_node_uuid: 'a', target_node_uuid: 'b' }],
  }
  assert.equal(graphPayloadRevision(payload), graphPayloadRevision({ ...payload, nodes: [...payload.nodes].reverse() }))
  assert.notEqual(graphPayloadRevision(payload), graphPayloadRevision({ ...payload, edges: [] }))
})
