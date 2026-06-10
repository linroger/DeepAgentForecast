/**
 * Minimal, dependency-free i18n for DeepAgentForecast.
 *
 * Usage in any component:
 *   import { locale, L, setLocale } from '<relative>/i18n'
 *   // in template: {{ L('实时日志', 'Live Log') }}
 *   // L() reads locale.value, so templates re-render when the locale changes.
 */
import { ref } from 'vue'

const STORAGE_KEY = 'drf_locale'

function initialLocale() {
  try {
    const s = localStorage.getItem(STORAGE_KEY)
    if (s === 'en' || s === 'zh') return s
  } catch (e) { /* noop */ }
  return 'zh'
}

export const locale = ref(initialLocale())

export function setLocale(l) {
  locale.value = l === 'en' ? 'en' : 'zh'
  try { localStorage.setItem(STORAGE_KEY, locale.value) } catch (e) { /* noop */ }
}

/** Pick a string by current locale. Reactive (reads locale.value). */
export function L(zh, en) {
  return locale.value === 'en' ? (en == null ? zh : en) : zh
}
