import assert from 'node:assert/strict'
import test from 'node:test'

import {
  detectTranslationTarget,
  createLangState,
  onReportIdentity,
  storeTranslation,
  selectLang,
  activeMarkdown,
} from '../src/utils/researchTranslation.js'

test('detectTranslationTarget picks the opposite language of the source', () => {
  assert.equal(detectTranslationTarget('English research report body.'), 'zh')
  assert.equal(detectTranslationTarget('这是一份中文研究报告，包含大量中文内容。'), 'en')
  assert.equal(detectTranslationTarget(''), 'zh') // empty → default to zh target
})

test('selecting a language shows its cached translation, else the source', () => {
  let state = createLangState()
  const source = '# EN source\n\nbody.'
  // No translation cached yet → source is shown even when zh is selected.
  state = selectLang(state, 'zh')
  assert.equal(activeMarkdown(state, source), source)
  // After a verified translation lands, the toggle shows it.
  state = storeTranslation(state, 'zh', '# 中文\n\n正文。')
  assert.equal(activeMarkdown(state, source), '# 中文\n\n正文。')
  // Switching back to the source (null) always shows the primary bytes.
  state = selectLang(state, null)
  assert.equal(activeMarkdown(state, source), source)
})

test('report-identity change clears the language cache (no stale cross-report bytes)', () => {
  // Report A: translate and view zh.
  let state = onReportIdentity(createLangState(), 'pipe_A')
  state = storeTranslation(state, 'zh', 'A 的中文译文。')
  state = selectLang(state, 'zh')
  assert.equal(activeMarkdown(state, 'A source'), 'A 的中文译文。')

  // Switching to report B MUST reset language + cache, so B never shows A's zh.
  const before = state
  state = onReportIdentity(state, 'pipe_B')
  assert.notEqual(state, before)
  assert.equal(state.activeLang, null)
  assert.deepEqual(state.cache, {})
  // Selecting zh on B before its translation loads falls back to B's source,
  // never A's cached bytes.
  state = selectLang(state, 'zh')
  assert.equal(activeMarkdown(state, 'B source'), 'B source')
})

test('re-entering the same report identity preserves its cache', () => {
  let state = onReportIdentity(createLangState(), 'pipe_A')
  state = storeTranslation(state, 'zh', 'A 中文')
  const same = onReportIdentity(state, 'pipe_A')
  assert.equal(same, state) // identity unchanged → same object, cache intact
  assert.equal(activeMarkdown(selectLang(same, 'zh'), 'A src'), 'A 中文')
})

test('storeTranslation rejects unsupported languages and empty markdown', () => {
  let state = createLangState()
  state = storeTranslation(state, 'fr', 'texte')
  assert.deepEqual(state.cache, {})
  state = storeTranslation(state, 'zh', '   ')
  assert.deepEqual(state.cache, {})
})
