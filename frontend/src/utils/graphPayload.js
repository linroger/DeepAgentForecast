// Shared graph-overview contract. Keep this default aligned with backend Config.GRAPH_UI_MAX_NODES.
export const DEFAULT_GRAPH_UI_MAX_NODES = 400

export function graphRequestParams(options = {}) {
  if (options.full === true) return { full: 'true' }
  const params = {}
  const requestedTopK = options.topK == null ? 0 : Number(options.topK)
  if (Number.isFinite(requestedTopK)) params.top_k = Math.floor(requestedTopK)
  if (options.slim !== false) params.slim = 'true'
  return params
}

export function graphPayloadMeta(raw, limit = DEFAULT_GRAPH_UI_MAX_NODES) {
  const nodes = Array.isArray(raw?.nodes) ? raw.nodes : []
  const edges = Array.isArray(raw?.edges) ? raw.edges : []
  const returnedNodes = nodes.length
  const returnedEdges = edges.length
  const totalNodes = Number.isFinite(Number(raw?.total_node_count))
    ? Number(raw.total_node_count)
    : returnedNodes
  const totalEdges = Number.isFinite(Number(raw?.total_edge_count))
    ? Number(raw.total_edge_count)
    : returnedEdges
  const cap = Number.isFinite(Number(limit)) && Number(limit) > 0
    ? Math.floor(Number(limit))
    : DEFAULT_GRAPH_UI_MAX_NODES
  const truncated = raw?.truncated === true || totalNodes > returnedNodes || totalEdges > returnedEdges
  return {
    nodes,
    edges,
    returnedNodes,
    returnedEdges,
    totalNodes,
    totalEdges,
    truncated,
    needsFocus: truncated || totalNodes > cap,
  }
}

// Structural revision used to invalidate focus/full-graph caches after polling.
// The overview is capped, so hashing its identifiers is bounded by the UI budget.
export function graphPayloadRevision(raw) {
  if (!raw || typeof raw !== 'object') return ''
  const nodes = Array.isArray(raw.nodes) ? raw.nodes : []
  const edges = Array.isArray(raw.edges) ? raw.edges : []
  const nodeParts = nodes
    .map(node => `${node?.uuid || ''}:${node?.name || ''}:${(node?.labels || []).join(',')}`)
    .sort()
  const edgeParts = edges
    .map(edge => `${edge?.uuid || ''}:${edge?.source_node_uuid || ''}:${edge?.target_node_uuid || ''}:${edge?.name || edge?.fact_type || ''}`)
    .sort()
  return [
    raw.graph_id || '',
    raw.total_node_count ?? raw.node_count ?? nodes.length,
    raw.total_edge_count ?? raw.edge_count ?? edges.length,
    nodeParts.join('|'),
    edgeParts.join('|'),
  ].join('::')
}

function rankIds(ids, degree) {
  return [...new Set(ids.filter(Boolean))].sort((left, right) => {
    const byDegree = (degree.get(right) || 0) - (degree.get(left) || 0)
    return byDegree || String(left).localeCompare(String(right))
  })
}

/**
 * Select a deterministic focus set that never exceeds the render budget.
 * The active center is retained first, followed by researched actors, expanded
 * neighbors, and finally the original degree-ranked overview.
 */
export function boundedFocusNodeIds({
  candidateNodes = [],
  edges = [],
  baseNodeIds = [],
  expandedNodeIds = [],
  seedNodeIds = [],
  activeNodeId = null,
  limit = DEFAULT_GRAPH_UI_MAX_NODES,
} = {}) {
  const cap = Number.isFinite(Number(limit)) && Number(limit) > 0
    ? Math.floor(Number(limit))
    : DEFAULT_GRAPH_UI_MAX_NODES
  const candidates = new Set(candidateNodes.map(node => node?.uuid).filter(Boolean))
  const degree = new Map()
  for (const edge of edges) {
    const source = edge?.source_node_uuid
    const target = edge?.target_node_uuid
    if (source) degree.set(source, (degree.get(source) || 0) + 1)
    if (target && target !== source) degree.set(target, (degree.get(target) || 0) + 1)
  }

  const kept = new Set()
  const add = id => {
    if (kept.size < cap && candidates.has(id)) kept.add(id)
  }
  add(activeNodeId)
  rankIds(seedNodeIds, degree).forEach(add)
  rankIds(expandedNodeIds, degree).forEach(add)
  rankIds(baseNodeIds, degree).forEach(add)
  return kept
}
