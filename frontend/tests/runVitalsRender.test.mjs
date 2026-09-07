import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

test('RunVitals renders unavailable, zero, and partial cumulative usage accurately', async (t) => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    plugins: [vue()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom'
  })
  try {
    const { default: RunVitals } = await server.ssrLoadModule('/src/components/research/RunVitals.vue')
    const { setLocale } = await server.ssrLoadModule('/src/i18n.js')
    setLocale('en')
    const render = (live) => renderToString(createSSRApp(RunVitals, { live, status: 'completed' }))

    await t.test('unavailable records render unknown spend and budget without invented numeric labels', async () => {
      const html = await render({
        spend_so_far: { available: false, tokens: null, cost_usd: null, usage_complete: false },
        budget: { available: false, limit_tokens: 1000, spent_tokens: null,
          remaining_tokens: null, usage_complete: false }
      })
      assert.match(html, /unknown tok/)
      assert.match(html, /unknown \/ 1K tokens/)
      assert.match(html, />unknown<\/span>/)
      assert.match(html, /rv-budget-fill[^>]*display:none/)
      assert.doesNotMatch(html, /\$0\.00|>0 tok|0%|null%|>1K<\/span>/)
    })

    await t.test('a registered zero renders actual zero and the observed budget remainder', async () => {
      const html = await render({
        spend_so_far: { available: true, tokens: 0, cost_usd: 0, usage_complete: false },
        budget: { available: true, limit_tokens: 1000, spent_tokens: 0,
          remaining_tokens: 1000, usage_complete: false }
      })
      assert.match(html, />0 tok/)
      assert.match(html, /\$0\.00/)
      assert.match(html, />1K<\/span>/)
      assert.match(html, /coverage is incomplete/)
      assert.doesNotMatch(html, /rv-budget-fill[^>]*display:none/)
      assert.doesNotMatch(html, /unknown/)
    })

    await t.test('cumulative usage displays recorded coverage rather than process-local claims', async () => {
      const html = await render({
        spend_so_far: { available: true, tokens: 1500, cost_usd: 0.04, usage_complete: false },
        budget: { available: true, limit_tokens: 10000, spent_tokens: 1500,
          remaining_tokens: 8500, usage_complete: false }
      })
      assert.match(html, /1\.5K tok/)
      assert.match(html, /\$0\.04/)
      assert.match(html, /Cumulative recorded usage/)
      assert.match(html, /actual spend may be higher/)
      assert.doesNotMatch(html, /backend process/)
    })
  } finally {
    await server.close()
  }
})
