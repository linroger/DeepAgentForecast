// Pure, framework-free helpers for the deep-research report bilingual toggle.
// Extracted from DossierViewer.vue so the language target detection and the
// report-identity cache-clear invariant can be unit-tested under `node --test`.

const SUPPORTED = new Set(['en', 'zh'])

/**
 * Sniff the source language of a research report and return the OTHER language as
 * the translation target.  A report dominated by CJK characters translates to
 * English; anything else (Latin-dominant) translates to Chinese.
 * @param {string} reportText
 * @returns {'en'|'zh'}
 */
export function detectTranslationTarget(reportText) {
  const text = String(reportText || '')
  if (!text) return 'zh'
  const cjk = (text.match(/[㐀-鿿]/g) || []).length
  const latin = (text.match(/[A-Za-z]/g) || []).length
  return cjk > latin ? 'en' : 'zh'
}

/**
 * Create a fresh language toggle state.  `reportKey` binds the cached translations
 * to one report identity; `activeLang === null` means "show the source".
 */
export function createLangState() {
  return { reportKey: null, activeLang: null, cache: {} }
}

/**
 * Return the state to use for a (possibly new) report identity.  When the identity
 * changes, a cleared state is returned so a second report can NEVER surface the
 * previous report's cached translation bytes — the report-identity guard.
 */
export function onReportIdentity(state, reportKey) {
  const prev = state || createLangState()
  if (prev.reportKey === reportKey) return prev
  return { reportKey, activeLang: null, cache: {} }
}

/** Immutably store a verified translation for a language. */
export function storeTranslation(state, lang, markdown) {
  const prev = state || createLangState()
  if (!SUPPORTED.has(lang) || typeof markdown !== 'string' || !markdown.trim()) {
    return prev
  }
  return { ...prev, cache: { ...prev.cache, [lang]: markdown } }
}

/** Immutably select the displayed language (null = source). */
export function selectLang(state, lang) {
  const prev = state || createLangState()
  return { ...prev, activeLang: lang === null ? null : lang }
}

/**
 * Resolve the markdown to display: the cached translation when a language is
 * selected AND available, otherwise the source markdown (never a stale variant).
 */
export function activeMarkdown(state, sourceMarkdown) {
  const prev = state || createLangState()
  const lang = prev.activeLang
  if (lang && prev.cache[lang]) return prev.cache[lang]
  return sourceMarkdown || ''
}
