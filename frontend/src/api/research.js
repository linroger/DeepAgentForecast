import service from './index'

/**
 * 启动统一研究→预测管线（Step 0）
 *
 * 注意：此请求 **非幂等** —— 每次 /run 都会新建一条 pipeline 并拉起一个 DeerFlow 子进程
 * 与一整轮 OASIS 模拟。因此绝不能用 requestWithRetry 包裹：一次因超时/网络抖动而丢失响应的
 * 重试会启动第二条不可见的管线，白白消耗 Claude 额度与算力。改用单次 service() 调用。
 * @param {Object} data { prompt, mode, project_name, depth, max_rounds }
 * @returns {Promise}
 */
export function runPipeline(data) {
  return service({
    url: '/api/research/run',
    method: 'post',
    data
  })
}

/**
 * 取消在飞管线（杀掉研究子进程 / 停止 OASIS 模拟）
 * @param {String} pipelineId
 * @returns {Promise}
 */
export function cancelPipeline(pipelineId) {
  return service({
    url: `/api/research/${pipelineId}/cancel`,
    method: 'post'
  })
}

/**
 * 恢复失败/取消的管线。后端会复用已完成产物并从第一个缺失/失败阶段继续。
 * @param {String} pipelineId
 * @returns {Promise}
 */
export function resumePipeline(pipelineId) {
  return service({
    url: `/api/research/${pipelineId}/resume`,
    method: 'post'
  })
}

/**
 * 把已完成的 research_only 管线继续跑成完整管线（复用研究产物，不重跑研究）。T6.2
 * @param {String} pipelineId
 * @returns {Promise}
 */
export function continuePipeline(pipelineId) {
  return service({
    url: `/api/research/${pipelineId}/continue`,
    method: 'post'
  })
}

/**
 * 在 PREPARE 处分叉一个 what-if 情景管线（复用 base 研究/本体/图谱）。T4.6
 * @param {String} pipelineId 基础管线
 * @param {Object} overlay { label, max_rounds?, influence_overrides?, stance_overrides?, injected_events?, as_of_shift? }
 * @returns {Promise}
 */
export function forkScenario(pipelineId, overlay) {
  return service({
    url: `/api/research/${pipelineId}/scenario`,
    method: 'post',
    data: overlay
  })
}

/**
 * 读取某阶段产物（dossier/timeline/sources/ontology/communities/initial_posts/personas/run_summary）。T6.3
 * @param {String} pipelineId
 * @param {String} name
 * @returns {Promise}
 */
export function getArtifact(pipelineId, name) {
  return service({
    url: `/api/research/${pipelineId}/artifact/${name}`,
    method: 'get'
  })
}

/**
 * 启动前就绪检查（不发起管线）。T5.6
 * @param {String} mode full | research_only
 * @returns {Promise}
 */
export function getPreflight(mode = 'full') {
  return service({
    url: '/api/research/preflight',
    method: 'get',
    params: { mode }
  })
}

/**
 * 删除一条已结束的管线记录（在飞管线须先取消，否则后端返回 409）
 * @param {String} pipelineId
 * @returns {Promise}
 */
export function deletePipeline(pipelineId) {
  return service({
    url: `/api/research/${pipelineId}`,
    method: 'delete'
  })
}

/**
 * 批量清理失败/已取消的管线记录
 * @param {Array<String>} statuses 默认 ['failed','cancelled']
 * @returns {Promise}
 */
export function cleanPipelines(statuses = ['failed', 'cancelled']) {
  return service({
    url: '/api/research/clean',
    method: 'post',
    data: { statuses }
  })
}

/**
 * 查询管线聚合进度
 * @param {String} pipelineId
 * @returns {Promise}
 */
export function getPipelineStatus(pipelineId) {
  return service({
    url: `/api/research/status/${pipelineId}`,
    method: 'get'
  })
}

/**
 * 管线列表
 */
export function listPipelines() {
  return service({ url: '/api/research/list', method: 'get' })
}

/**
 * 研究产出（research_report.md + actors/sources）
 * @param {String} pipelineId
 */
export function getDossier(pipelineId) {
  return service({
    url: `/api/research/${pipelineId}/dossier`,
    method: 'get'
  })
}

/**
 * 编辑研究档案（research_report.md / actors.json）。仅完成的 research_only 或建图前失败管线可编辑。T5.4
 * @param {String} pipelineId
 * @param {Object} payload { report?: string, actors?: object }
 */
export function editDossier(pipelineId, payload) {
  return service({
    url: `/api/research/${pipelineId}/dossier`,
    method: 'put',
    data: payload
  })
}

/**
 * 研究子进程进度日志（tail）
 * @param {String} pipelineId
 * @param {Number} lines
 */
export function getProgressLog(pipelineId, lines = 200) {
  return service({
    url: `/api/research/${pipelineId}/progress`,
    method: 'get',
    params: { lines }
  })
}
