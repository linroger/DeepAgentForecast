import service from './index'
import { graphRequestParams } from '../utils/graphPayload'

// LOOP-003B：所有 UI 初始图谱请求默认走服务端 overview（top_k=0 读取后端配置上限）
// + slim 载荷。完整重字段图谱只在调用方显式 full:true 时下载。

// EXECPLAN2 F-10-12: 本体生成 / 建图为非幂等 POST（触发 LLM 抽取与建图副作用），不自动重试。

/**
 * 生成本体（上传文档和模拟需求）
 * @param {Object} data - 包含files, simulation_requirement, project_name等
 * @returns {Promise}
 */
export function generateOntology(formData) {
  return service({   // non-idempotent: do not retry
    url: '/api/graph/ontology/generate',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

/**
 * 构建图谱
 * @param {Object} data - 包含project_id, graph_name等
 * @returns {Promise}
 */
export function buildGraph(data) {
  return service({   // non-idempotent: do not retry
    url: '/api/graph/build',
    method: 'post',
    data
  })
}

/**
 * 查询任务状态
 * @param {String} taskId - 任务ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * 获取图谱数据
 * @param {String} graphId - 图谱ID
 * @param {Object} [options] - 可选的服务端过滤参数（KG 性能优化：大图只取 Top-K 子图/瘦身载荷）
 *   - topK {Number}  只返回按度数排名前 K 的节点及其诱导边（后端 ?top_k=）
 *   - slim {Boolean} 去掉 summary/fact/episodes 等重字段的瘦身载荷（后端 ?slim=true）
 *   旧版后端会忽略未知查询参数并返回完整图谱，调用方需按节点数自行降级处理。
 * @returns {Promise}
 */
export function getGraphData(graphId, options = {}) {
  const params = graphRequestParams(options)
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get',
    params: Object.keys(params).length ? params : undefined
  })
}

export function getGraphNodeDetail(graphId, nodeUuid) {
  return service({
    url: `/api/graph/data/${graphId}/node/${encodeURIComponent(nodeUuid)}`,
    method: 'get'
  })
}

export function getGraphEdgeDetail(graphId, edgeUuid) {
  return service({
    url: `/api/graph/data/${graphId}/edge/${encodeURIComponent(edgeUuid)}`,
    method: 'get'
  })
}

/**
 * 获取某节点的 N 跳邻居子图（聚焦模式点击展开用）
 * 后端端点：GET /api/graph/data/<graph_id>/neighbors/<node_uuid>?depth=1
 * 旧版后端没有该端点时会返回 404，调用方应捕获并回退到本地（完整载荷）展开。
 * @param {String} graphId - 图谱ID
 * @param {String} nodeUuid - 中心节点UUID
 * @param {Number} [depth=1] - 邻居深度
 * @returns {Promise}
 */
export function getGraphNeighbors(graphId, nodeUuid, depth = 1) {
  return service({
    url: `/api/graph/data/${graphId}/neighbors/${encodeURIComponent(nodeUuid)}`,
    method: 'get',
    params: { depth }
  })
}

/**
 * 获取项目信息
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function getProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}
