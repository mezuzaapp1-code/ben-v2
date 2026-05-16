/** Request-local dominant language detection (mirrors services/language_context.py). */

const HEBREW_RE = /[\u0590-\u05FF]/g
const ARABIC_RE = /[\u0600-\u06FF\u0750-\u077F]/g
const LATIN_RE = /[A-Za-z]/g
const MIXED_RATIO = 0.35

function countScripts(text) {
  const he = (text.match(HEBREW_RE) || []).length
  const ar = (text.match(ARABIC_RE) || []).length
  const en = (text.match(LATIN_RE) || []).length
  return { he, ar, en }
}

/**
 * @returns {{ dominant_language: string, text_direction: 'ltr'|'rtl', label_locale: string }}
 */
export function detectDominantLanguage(text) {
  const t = (text || '').trim()
  if (!t) {
    return { dominant_language: 'en', text_direction: 'ltr', label_locale: 'en' }
  }
  const counts = countScripts(t)
  const total = counts.he + counts.ar + counts.en
  if (total === 0) {
    return { dominant_language: 'en', text_direction: 'ltr', label_locale: 'en' }
  }
  const ranked = [
    ['he', counts.he],
    ['ar', counts.ar],
    ['en', counts.en],
  ].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  const [topLang, topN] = ranked[0]
  const secondN = ranked[1][1]
  if (topN === 0) {
    return { dominant_language: 'en', text_direction: 'ltr', label_locale: 'en' }
  }
  if (secondN > 0 && secondN >= topN * MIXED_RATIO) {
    const rtl = counts.he + counts.ar >= counts.en
    return { dominant_language: 'mixed', text_direction: rtl ? 'rtl' : 'ltr', label_locale: 'en' }
  }
  const dominant = topLang === 'he' ? 'he' : topLang === 'ar' ? 'ar' : 'en'
  const text_direction = dominant === 'he' || dominant === 'ar' ? 'rtl' : 'ltr'
  const label_locale = dominant === 'mixed' || dominant === 'unknown' ? 'en' : dominant
  return { dominant_language: dominant, text_direction, label_locale }
}

export function isMeaningfulReasoningValue(val) {
  if (val == null) return false
  if (typeof val === 'object') return Array.isArray(val) ? val.length > 0 : Object.keys(val).length > 0
  const s = String(val).trim()
  if (!s) return false
  const low = s.toLowerCase()
  if (low === 'null' || low === 'none' || low === 'n/a' || low === 'na') return false
  return true
}

export function bubbleTextProps(textOrCtx) {
  const ctx =
    typeof textOrCtx === 'object' && textOrCtx?.text_direction
      ? textOrCtx
      : detectDominantLanguage(typeof textOrCtx === 'string' ? textOrCtx : '')
  const rtl = ctx.text_direction === 'rtl'
  return {
    dir: rtl ? 'rtl' : 'ltr',
    className: rtl ? 'bubble-text bubble-text--rtl' : 'bubble-text bubble-text--ltr',
  }
}
