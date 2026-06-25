/** Detect whether message text is primarily Hebrew (RTL). */
export function isRtlMarkdown(text) {
  const sample = String(text ?? '').slice(0, 2400)
  if (!sample.trim()) return false
  const hebrew = (sample.match(/[\u0590-\u05FF]/g) || []).length
  const latin = (sample.match(/[A-Za-z]/g) || []).length
  return hebrew > 0 && hebrew >= latin
}

/** Resolve reading direction for channel alignment (`rtl` | `ltr`). */
export function getMessageTextDirection(text) {
  return isRtlMarkdown(text) ? 'rtl' : 'ltr'
}
