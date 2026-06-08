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
