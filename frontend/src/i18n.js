/**
 * Minimal, dependency-free i18n for DeepResearchForecast.
 *
 * Usage in any component:
 *   import { locale, L, setLocale } from '<relative>/i18n'
 *   // in template: {{ L('实时日志', 'Live Log') }}
 *   // L() reads locale.value, so templates re-render when the locale changes.
 */
import { ref } from 'vue'

const STORAGE_KEY = 'drf_locale'

const DOC_TITLES = {
  zh: 'DeepResearchForecast · 深度研究与预测',
  en: 'DeepResearchForecast · Deep Research & Forecast'
}

function initialLocale() {
  try {
    const s = localStorage.getItem(STORAGE_KEY)
    if (s === 'en' || s === 'zh') return s
  } catch (e) { /* noop */ }
  // No persisted choice: derive from the browser language (zh* → zh, else en).
  try {
    const nav = typeof navigator !== 'undefined' ? (navigator.language || '') : ''
    return /^zh/i.test(nav) ? 'zh' : 'en'
  } catch (e) { /* noop */ }
  return 'zh'
}

export const locale = ref(initialLocale())

/** Keep <html lang> and the document title in sync with the active locale. */
function syncDocumentLocale() {
  if (typeof document === 'undefined') return
  document.documentElement.lang = locale.value === 'en' ? 'en' : 'zh-CN'
  document.title = locale.value === 'en' ? DOC_TITLES.en : DOC_TITLES.zh
}

export function setLocale(l) {
  locale.value = l === 'en' ? 'en' : 'zh'
  try { localStorage.setItem(STORAGE_KEY, locale.value) } catch (e) { /* noop */ }
  syncDocumentLocale()
}

// Apply once at module init so the initial page load matches the derived locale.
syncDocumentLocale()

/** Pick a string by current locale. Reactive (reads locale.value). */
export function L(zh, en) {
  return locale.value === 'en' ? (en == null ? zh : en) : zh
}
