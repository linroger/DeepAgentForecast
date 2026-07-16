const SUPPORTED = new Set(['en', 'zh'])
const SHA256_RE = /^[a-f0-9]{64}$/i

const ACTIVE_TRANSLATION_STATUSES = new Set([
  'pending', 'processing', 'generating', 'queued', 'running', 'in_progress',
  'in-progress', 'starting',
])
const SUCCESS_TRANSLATION_STATUSES = new Set([
  'available', 'completed', 'complete', 'succeeded', 'success',
])
const TERMINAL_TRANSLATION_STATUSES = new Set([
  'failed', 'interrupted', 'invalid', 'stale', 'unavailable', 'missing',
  'not_found', 'unsupported', 'source_language', 'blocked', 'disabled',
  'cancelled', 'canceled', 'aborted', 'expired', 'error',
])

function objectOrEmpty(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function codeOf(value) {
  return String(value || '').trim().toLowerCase()
}

function isSha256(value) {
  return SHA256_RE.test(String(value || '').trim())
}

function verifiedTranslationContext(meta) {
  const state = objectOrEmpty(meta?.translation_status)
  const verified = objectOrEmpty(state.translation)
  const reportId = String(meta?.report_id || '').trim()
  const sourceLang = codeOf(state.source_lang)
  const targetLang = codeOf(state.target_lang)
  const requestedLang = codeOf(state.requested_lang)
  const markdownSha = String(state.markdown_sha256 || '').trim()

  if (!reportId || String(state.report_id || '').trim() !== reportId) return null
  if (codeOf(state.status) !== 'available' || state.available !== true) return null
  if (!SUPPORTED.has(sourceLang) || !SUPPORTED.has(targetLang) || sourceLang === targetLang) return null
  if (requestedLang && requestedLang !== targetLang) return null
  if (!isSha256(markdownSha)) return null
  if (verified.available !== true
      || codeOf(verified.lang) !== targetLang
      || codeOf(verified.source_lang) !== sourceLang
      || String(verified.path || '') !== `full_report.${targetLang}.md`
      || String(verified.markdown_sha256 || '').trim() !== markdownSha) {
    return null
  }
  return { reportId, sourceLang, targetLang, markdownSha }
}

function isVerifiedTranslationRow(entry, context) {
  const row = objectOrEmpty(entry)
  if (row.available !== true) return false
  if (String(row.report_id || '').trim() !== context.reportId) return false
  if (codeOf(row.lang) !== context.targetLang) return false
  if (codeOf(row.source_lang) !== context.sourceLang) return false
  if (!isSha256(row.source_markdown_sha256)) return false
  if (String(row.markdown_sha256 || '').trim() !== context.markdownSha) return false
  if (String(row.path || '') !== `full_report.${context.targetLang}.md`) return false
  if (String(row.citations_path || '') !== `citations.${context.targetLang}.json`) return false
  if (String(row.final_audit_path || '') !== `final_audit.${context.targetLang}.json`) return false
  return true
}

export function normalizeReportTranslations(meta) {
  const context = verifiedTranslationContext(meta)
  if (!context) return []
  const rows = Array.isArray(meta?.translations) ? meta.translations : []
  const seen = new Set()
  return rows.filter(entry => {
    if (!isVerifiedTranslationRow(entry, context)) return false
    const lang = codeOf(entry.lang)
    if (seen.has(lang)) return false
    seen.add(lang)
    return true
  })
}

export function reportLanguageOptions(meta, labelFor) {
  const translations = normalizeReportTranslations(meta)
  if (!translations.length) return []
  const source = codeOf(translations[0]?.source_lang)
  const label = typeof labelFor === 'function' ? labelFor : code => code || 'Original'
  return [
    { key: null, code: source, label: source ? label(source) : label('') },
    ...translations.map(entry => {
      const lang = codeOf(entry.lang)
      return { key: lang, code: lang, label: label(lang) }
    }),
  ]
}

export function reportTranslationIssues(payload) {
  const data = objectOrEmpty(payload)
  const result = objectOrEmpty(data.result)
  const issues = []
  const seen = new Set()
  const append = value => {
    if (Array.isArray(value)) {
      value.forEach(append)
      return
    }
    const text = typeof value === 'string' ? value.trim() : ''
    if (!text || seen.has(text)) return
    seen.add(text)
    issues.push(text)
  }
  append(data.error)
  append(data.issues)
  append(result.error)
  append(result.issues)
  return issues.slice(0, 12)
}

export function reportTranslationAction(meta) {
  const state = objectOrEmpty(meta?.translation_status)
  const reportId = String(meta?.report_id || '').trim()
  if (!reportId || String(state.report_id || '').trim() !== reportId) return null
  const targetLang = codeOf(state.target_lang)
  if (!SUPPORTED.has(targetLang)) return null
  const available = normalizeReportTranslations(meta)
    .some(entry => codeOf(entry.lang) === targetLang)
  if (available || state.can_generate !== true) return null
  const status = codeOf(state.status) || 'missing'
  return {
    targetLang,
    status,
    issues: reportTranslationIssues(state),
    retry: status !== 'missing',
  }
}

export function reportTranslationPollOutcome(payload, expectedTaskId = '') {
  const data = objectOrEmpty(payload)
  const result = objectOrEmpty(data.result)
  const status = codeOf(data.status)
  const expected = String(expectedTaskId || '').trim()
  const actual = String(data.task_id || '').trim()
  const available = data.available === true || result.available === true
  const canGenerate = data.can_generate === true
  const issues = reportTranslationIssues(data)

  if (available) {
    return {
      terminal: true,
      available: true,
      canGenerate,
      status: 'available',
      issues: [],
      reason: 'available',
    }
  }

  if (ACTIVE_TRANSLATION_STATUSES.has(status)) {
    if (expected && !actual) {
      return {
        terminal: true,
        available: false,
        canGenerate,
        status: 'interrupted',
        issues,
        reason: 'task_missing',
      }
    }
    if (expected && actual !== expected) {
      return {
        terminal: true,
        available: false,
        canGenerate,
        status: 'invalid',
        issues,
        reason: 'task_mismatch',
      }
    }
    return {
      terminal: false,
      available: false,
      canGenerate,
      status,
      issues,
      reason: 'active',
    }
  }

  if (SUCCESS_TRANSLATION_STATUSES.has(status)) {
    return {
      terminal: true,
      available: false,
      canGenerate,
      status,
      issues,
      reason: 'terminal_without_artifact',
    }
  }

  if (TERMINAL_TRANSLATION_STATUSES.has(status)) {
    return {
      terminal: true,
      available: false,
      canGenerate,
      status,
      issues,
      reason: 'terminal_status',
    }
  }

  return {
    terminal: true,
    available: false,
    canGenerate,
    status: 'invalid',
    issues,
    reason: 'unknown_status',
  }
}

export function createLatestRequestGate(
  controllerFactory = () => new AbortController(),
) {
  if (typeof controllerFactory !== 'function') {
    throw new TypeError('controllerFactory must be a function')
  }
  let generation = 0
  let active = null

  const abortActive = () => {
    if (!active) return
    const token = active
    active = null
    token.settled = true
    try { token.controller.abort() } catch (_) { /* cancellation is best-effort */ }
  }

  const isCurrent = token => Boolean(
    token
    && active === token
    && token.generation === generation
    && token.settled !== true
    && token.signal?.aborted !== true,
  )

  return {
    begin() {
      abortActive()
      generation += 1
      const controller = controllerFactory()
      if (!controller || typeof controller.abort !== 'function' || !controller.signal) {
        throw new TypeError('controllerFactory must return an AbortController-like object')
      }
      active = {
        generation,
        controller,
        signal: controller.signal,
        settled: false,
      }
      return active
    },
    isCurrent,
    finish(token) {
      if (!isCurrent(token)) return false
      token.settled = true
      active = null
      return true
    },
    cancel() {
      generation += 1
      abortActive()
    },
  }
}

export function downloadFilenameFromDisposition(value, fallback) {
  const raw = String(value || '')
  let filename = ''
  const encoded = raw.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)
  if (encoded) {
    try { filename = decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, '')) } catch (_) { /* fallback */ }
  }
  if (!filename) {
    const plain = raw.match(/filename\s*=\s*(?:"([^"]+)"|([^;\s]+))/i)
    filename = plain ? (plain[1] || plain[2] || '') : ''
  }
  filename = filename.replace(/[\\/\u0000-\u001f\u007f]/g, '_').trim()
  return filename || fallback
}

export function hasPdfMagic(bytes) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || 0)
  return view.length >= 5
    && view[0] === 0x25 && view[1] === 0x50 && view[2] === 0x44
    && view[3] === 0x46 && view[4] === 0x2d
}
