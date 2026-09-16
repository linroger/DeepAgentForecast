import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveBackendTarget } from '../devServerConfig.mjs'
import * as devServerConfig from '../devServerConfig.mjs'

test('development backend defaults and custom ports match the launcher', () => {
  assert.equal(resolveBackendTarget({}), 'http://127.0.0.1:5001')
  assert.equal(resolveBackendTarget({ FLASK_PORT: '' }), 'http://127.0.0.1:5001')
  for (const port of ['1', '5001', '18745', '65535', '05001']) {
    assert.equal(resolveBackendTarget({ FLASK_PORT: port }), `http://127.0.0.1:${Number(port)}`)
  }
  assert.equal(resolveBackendTarget({ FLASK_PORT: '5001', DRF_LAUNCHER_BACKEND_PORT: '18745' }), 'http://127.0.0.1:18745')
})

test('invalid explicit backend ports fail clearly', () => {
  for (const port of ['0', '-1', '65536', '5001.5', 'Infinity', 'NaN', ' 5001', '5001 ', '5e3', 'localhost:5001', '99999999999999999999999999']) {
    assert.throws(() => resolveBackendTarget({ FLASK_PORT: port }), /integer from 1 through 65535/)
    assert.throws(() => resolveBackendTarget({ FLASK_PORT: '5001', DRF_LAUNCHER_BACKEND_PORT: port }), /integer from 1 through 65535/)
  }
})

test('actual Vite configuration keeps UI and health on the selected backend without opening a browser', async () => {
  const previous = process.env.DRF_LAUNCHER_BACKEND_PORT
  try {
    process.env.DRF_LAUNCHER_BACKEND_PORT = '18745'
    const { default: config } = await import('../vite.config.js')
    assert.equal(config.server.port, 3000)
    assert.equal(config.server.strictPort, true)
    assert.equal(config.server.open, false)
    assert.equal(config.server.proxy['/api'].target, 'http://127.0.0.1:18745')
    assert.equal(config.server.proxy['/health'].target, config.server.proxy['/api'].target)
    assert.ok(config.plugins.some(plugin => plugin.name === 'drf-dev-routing-identity'))
  } finally {
    if (previous === undefined) delete process.env.DRF_LAUNCHER_BACKEND_PORT
    else process.env.DRF_LAUNCHER_BACKEND_PORT = previous
  }
})

function routingFixture(proxy) {
  let middleware
  const server = { config: { server: { proxy } }, middlewares: { use: handler => { middleware = handler } } }
  const plugin = devServerConfig.devRoutingIdentityPlugin()
  assert.equal(plugin.apply, 'serve')
  plugin.configureServer(server)
  return {
    server,
    request(url = '/__drf/dev-routing', method = 'GET') {
      const response = { headers: {}, statusCode: 0, body: '', next: false,
        setHeader(name, value) { this.headers[name] = value },
        end(body) { this.body = body } }
      middleware({ url, method }, response, () => { response.next = true })
      return response
    }
  }
}

test('routing identity reads actual resolved proxy targets on each request', () => {
  const fixture = routingFixture({ '/api': { target: 'http://127.0.0.1:5001' }, '/health': 'http://127.0.0.1:5001' })
  assert.deepEqual(JSON.parse(fixture.request().body), {
    schema: 'drf-dev-routing/v1', service: 'DeepResearchForecast Frontend',
    api_target: 'http://127.0.0.1:5001', health_target: 'http://127.0.0.1:5001'
  })
  fixture.server.config.server.proxy['/api'].target = 'http://127.0.0.1:18745'
  const response = fixture.request('/__drf/dev-routing?fresh=1')
  assert.equal(JSON.parse(response.body).api_target, 'http://127.0.0.1:18745')
  assert.equal(JSON.parse(response.body).health_target, 'http://127.0.0.1:5001')
  assert.equal(response.headers['Cache-Control'], 'no-store')
  assert.equal(response.statusCode, 200)
  assert.equal(fixture.request('/api/research').next, true)
  assert.equal(fixture.request('/__drf/dev-routing/other').next, true)
  assert.equal(fixture.request('/__drf/dev-routing', 'POST').statusCode, 405)
})

test('routing identity never exposes credentials, private hosts, or proxy options', () => {
  for (const target of [undefined, null, {}, 'invalid', 'http://user:secret@127.0.0.1:5001',
    'http://127.0.0.1:5001/?key=secret', 'http://127.0.0.1:5001/#secret',
    'http://127.0.0.1:5001/secret', 'http://private.internal:5001', 'https://127.0.0.1:5001']) {
    const fixture = routingFixture({ '/api': { target, headers: { Authorization: 'secret' } } })
    const response = fixture.request()
    assert.deepEqual(JSON.parse(response.body), {
      schema: 'drf-dev-routing/v1', service: 'DeepResearchForecast Frontend',
      api_target: null, health_target: null
    })
    assert.equal(response.body.includes('secret'), false)
    assert.equal(response.body.includes('private.internal'), false)
  }
})
