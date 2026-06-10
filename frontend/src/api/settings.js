import service from './index'

/** 当前 LLM 提供方 + 受支持提供方清单 */
export function getLlmSettings() {
  return service({ url: '/api/settings/llm', method: 'get' })
}

/**
 * 切换 LLM 提供方（对新发起的管线生效，无需重启）
 * @param {Object} data { provider, api_key?, base_url?, model? }
 */
export function setLlmSettings(data) {
  return service({ url: '/api/settings/llm', method: 'post', data })
}

/**
 * 测试提供方连通性（不持久化任何配置）。
 * CLI 提供方检查本机 CLI；API 提供方发一条最小 chat completion 验证 Key。
 * @param {Object} data { provider, api_key?, base_url?, model? }
 */
export function testLlmSettings(data) {
  return service({ url: '/api/settings/llm/test', method: 'post', data, timeout: 45000 })
}
