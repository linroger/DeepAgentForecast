import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createLatestRequestGate,
  downloadFilenameFromDisposition,
  hasPdfMagic,
  normalizeReportTranslations,
  reportLanguageOptions,
  reportTranslationAction,
  reportTranslationIssues,
  reportTranslationPollOutcome,
} from '../src/utils/reportLanguages.js'

const SOURCE_SHA = 'a'.repeat(64)
const TARGET_SHA = 'b'.repeat(64)

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function verifiedMeta() {
  const row = {
    report_id: 'report_123',
    lang: 'zh',
    source_lang: 'en',
    source_markdown_sha256: SOURCE_SHA,
    markdown_sha256: TARGET_SHA,
    path: 'full_report.zh.md',
    citations_path: 'citations.zh.json',
    final_audit_path: 'final_audit.zh.json',
    available: true,
  }
  return {
    report_id: 'report_123',
    translations: [row, { ...row }],
    translation_status: {
      report_id: 'report_123',
      source_lang: 'en',
      target_lang: 'zh',
      requested_lang: 'zh',
      status: 'available',
      available: true,
      can_generate: false,
      markdown_sha256: TARGET_SHA,
      issues: [],
      translation: {
        lang: 'zh',
        source_lang: 'en',
        path: 'full_report.zh.md',
        markdown_sha256: TARGET_SHA,
        available: true,
      },
    },
  }
}

test('language options expose only the current backend-verified publication row', () => {
  const meta = verifiedMeta()

  assert.deepEqual(normalizeReportTranslations(meta).map(row => row.lang), ['zh'])
  assert.deepEqual(
    reportLanguageOptions(meta, code => ({ en: 'EN', zh: '中文' }[code] || 'Original')),
    [
      { key: null, code: 'en', label: 'EN' },
      { key: 'zh', code: 'zh', label: '中文' },
    ],
  )
})

test('missing availability and stale or unaudited translation rows never become toggles', () => {
  const cases = [
    ['missing explicit available', meta => { delete meta.translations[0].available; delete meta.translations[1].available }],
    ['wrong report identity', meta => { meta.translations.forEach(row => { row.report_id = 'report_old' }) }],
    ['missing source fingerprint', meta => { meta.translations.forEach(row => { delete row.source_markdown_sha256 }) }],
    ['missing target fingerprint', meta => { meta.translations.forEach(row => { delete row.markdown_sha256 }) }],
    ['missing citation identity', meta => { meta.translations.forEach(row => { delete row.citations_path }) }],
    ['missing final audit identity', meta => { meta.translations.forEach(row => { delete row.final_audit_path }) }],
    ['stale backend status', meta => {
      meta.translation_status.status = 'stale'
      meta.translation_status.available = false
    }],
    ['missing backend verification marker', meta => { delete meta.translation_status.translation }],
    ['verification fingerprint mismatch', meta => {
      meta.translation_status.translation.markdown_sha256 = 'c'.repeat(64)
    }],
  ]

  for (const [name, mutate] of cases) {
    const meta = clone(verifiedMeta())
    mutate(meta)
    assert.deepEqual(normalizeReportTranslations(meta), [], name)
    assert.deepEqual(reportLanguageOptions(meta), [], name)
  }
})

test('translation action is exposed strictly according to can_generate', () => {
  const failed = reportTranslationAction({
    report_id: 'report_123',
    translation_status: {
      report_id: 'report_123',
      source_lang: 'en',
      target_lang: 'zh',
      status: 'failed',
      available: false,
      can_generate: true,
      issues: ['numeric parity'],
    },
  })

  assert.deepEqual(failed, {
    targetLang: 'zh',
    status: 'failed',
    issues: ['numeric parity'],
    retry: true,
  })
  assert.equal(reportTranslationAction(verifiedMeta()), null)
  assert.equal(reportTranslationAction({
    report_id: 'report_123',
    translation_status: {
      report_id: 'report_123',
      target_lang: 'zh', available: false, can_generate: false,
    },
  }), null)
  assert.equal(reportTranslationAction({
    report_id: 'report_123',
    translation_status: {
      report_id: 'report_123',
      target_lang: 'zh', available: false, can_generate: 'true',
    },
  }), null)
  assert.equal(reportTranslationAction({
    report_id: 'report_123',
    translation_status: {
      report_id: 'report_old',
      target_lang: 'zh', available: false, can_generate: true,
    },
  }), null)

  const stale = clone(verifiedMeta())
  stale.translation_status.status = 'stale'
  stale.translation_status.available = false
  stale.translation_status.can_generate = true
  stale.translation_status.issues = ['translation audit belongs to older report bytes']
  assert.equal(reportTranslationAction(stale)?.retry, true)

  const missing = reportTranslationAction({
    report_id: 'report_123',
    translation_status: {
      report_id: 'report_123',
      target_lang: 'zh',
      status: 'missing',
      available: false,
      can_generate: true,
    },
  })
  assert.equal(missing?.retry, false)
})

test('translation polling recognizes every terminal state and equivalent', () => {
  const failures = [
    'failed', 'interrupted', 'invalid', 'stale', 'unavailable',
    'blocked', 'disabled', 'unsupported', 'not_found', 'source_language',
    'cancelled', 'canceled', 'aborted', 'expired', 'error',
  ]
  for (const status of failures) {
    const outcome = reportTranslationPollOutcome({
      status,
      available: false,
      can_generate: true,
      issues: [`${status} issue`],
    }, 'task_1')
    assert.equal(outcome.terminal, true, status)
    assert.equal(outcome.available, false, status)
    assert.equal(outcome.canGenerate, true, status)
    assert.deepEqual(outcome.issues, [`${status} issue`], status)
  }

  const available = reportTranslationPollOutcome({
    status: 'available', available: true, can_generate: false,
  }, 'task_1')
  assert.equal(available.terminal, true)
  assert.equal(available.available, true)

  const completed = reportTranslationPollOutcome({
    status: 'completed',
    available: false,
    can_generate: false,
    result: { available: true },
  }, 'task_1')
  assert.equal(completed.terminal, true)
  assert.equal(completed.available, true)

  const completedWithoutArtifact = reportTranslationPollOutcome({
    status: 'completed', available: false, can_generate: true,
  }, 'task_1')
  assert.equal(completedWithoutArtifact.terminal, true)
  assert.equal(completedWithoutArtifact.available, false)
  assert.equal(completedWithoutArtifact.reason, 'terminal_without_artifact')
})

test('translation polling continues only for a recognized active state bound to the task', () => {
  for (const status of ['pending', 'processing', 'generating', 'queued', 'running', 'in_progress']) {
    const outcome = reportTranslationPollOutcome({
      status,
      task_id: 'task_1',
      available: false,
      can_generate: false,
    }, 'task_1')
    assert.equal(outcome.terminal, false, status)
    assert.equal(outcome.available, false, status)
  }

  const restartedBackend = reportTranslationPollOutcome({
    status: 'generating',
    available: false,
    can_generate: false,
  }, 'task_1')
  assert.equal(restartedBackend.terminal, true)
  assert.equal(restartedBackend.status, 'interrupted')
  assert.equal(restartedBackend.reason, 'task_missing')

  const wrongTask = reportTranslationPollOutcome({
    status: 'processing',
    task_id: 'task_2',
    available: false,
    can_generate: true,
  }, 'task_1')
  assert.equal(wrongTask.terminal, true)
  assert.equal(wrongTask.status, 'invalid')
  assert.equal(wrongTask.reason, 'task_mismatch')

  const unknown = reportTranslationPollOutcome({
    status: 'mystery', available: false, can_generate: true,
  }, 'task_1')
  assert.equal(unknown.terminal, true)
  assert.equal(unknown.status, 'invalid')
  assert.equal(unknown.reason, 'unknown_status')
})

test('translation issues are durable, normalized, and deduplicated', () => {
  assert.deepEqual(reportTranslationIssues({
    error: 'worker stopped',
    issues: ['numeric parity', 'numeric parity', null],
    result: { issues: ['missing references'] },
  }), ['worker stopped', 'numeric parity', 'missing references'])
})

test('latest request gate aborts and rejects stale export cleanup', () => {
  const gate = createLatestRequestGate()
  const first = gate.begin()
  assert.equal(gate.isCurrent(first), true)

  const second = gate.begin()
  assert.equal(first.signal.aborted, true)
  assert.equal(gate.isCurrent(first), false)
  assert.equal(gate.finish(first), false)
  assert.equal(gate.isCurrent(second), true)
  assert.equal(gate.finish(second), true)
  assert.equal(gate.isCurrent(second), false)

  const third = gate.begin()
  gate.cancel()
  assert.equal(third.signal.aborted, true)
  assert.equal(gate.isCurrent(third), false)
  assert.equal(gate.finish(third), false)
})

test('download validation preserves server filenames and rejects non-PDF bytes', () => {
  assert.equal(
    downloadFilenameFromDisposition(
      "attachment; filename*=UTF-8''report_123.zh.pdf",
      'fallback.pdf',
    ),
    'report_123.zh.pdf',
  )
  assert.equal(
    downloadFilenameFromDisposition('attachment; filename="../bad.pdf"', 'fallback.pdf'),
    '.._bad.pdf',
  )
  assert.equal(downloadFilenameFromDisposition('', 'fallback.pdf'), 'fallback.pdf')
  assert.equal(hasPdfMagic(new TextEncoder().encode('%PDF-1.7')), true)
  assert.equal(hasPdfMagic(new TextEncoder().encode('<html>error</html>')), false)
})
