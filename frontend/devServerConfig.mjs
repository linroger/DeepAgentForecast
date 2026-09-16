/** Keep the development proxy on the same port selected by scripts/start.sh. */
export function resolveBackendTarget(env = process.env) {
  const raw = env.DRF_LAUNCHER_BACKEND_PORT || env.FLASK_PORT || '5001'
  const port = Number(raw)
  if (typeof raw !== 'string' || !/^\d+$/.test(raw) || !Number.isSafeInteger(port) || port < 1 || port > 65535) {
    throw new Error('Backend port must be an integer from 1 through 65535 (DRF_LAUNCHER_BACKEND_PORT or FLASK_PORT).')
  }
  return `http://127.0.0.1:${port}`
}

function publicLoopbackTarget(proxy) {
  const target = typeof proxy === 'string' ? proxy : proxy?.target
  if (typeof target !== 'string') return null
  try {
    const url = new URL(target)
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || url.pathname !== '/') return null
    // Only the loopback origin is public. Never serialize arbitrary proxy
    // options, credentials, paths, queries, or private hostnames.
    return `http://${url.hostname}:${url.port || '80'}`
  } catch {
    return null
  }
}

export function devRoutingIdentityPlugin() {
  return {
    name: 'drf-dev-routing-identity',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url?.split('?')[0] !== '/__drf/dev-routing') return next()
        response.setHeader('Content-Type', 'application/json; charset=utf-8')
        response.setHeader('Cache-Control', 'no-store')
        if (request.method !== 'GET') {
          response.statusCode = 405
          response.setHeader('Allow', 'GET')
          response.end(JSON.stringify({ error: 'Method not allowed' }))
          return
        }
        // Read Vite's resolved configuration, including overrides, rather than
        // reporting the initial environment-derived target captured by config.
        const proxy = server.config.server.proxy || {}
        response.statusCode = 200
        response.end(JSON.stringify({
          schema: 'drf-dev-routing/v1',
          service: 'DeepResearchForecast Frontend',
          api_target: publicLoopbackTarget(proxy['/api']),
          health_target: publicLoopbackTarget(proxy['/health'])
        }))
      })
    }
  }
}
