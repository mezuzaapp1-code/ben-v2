const RTL_SCRIPT = /[\u0590-\u05FF\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/g

/** Detect whether message text is primarily Hebrew/Arabic (RTL). Chrome must never use this. */
export function isRtlMarkdown(text) {
  const sample = String(text ?? '').slice(0, 2400)
  if (!sample.trim()) return false
  const rtl = (sample.match(RTL_SCRIPT) || []).length
  const latin = (sample.match(/[A-Za-z]/g) || []).length
  return rtl > 0 && rtl >= latin
}

/** Resolve reading direction for content (`rtl` | `ltr`). Never used for app chrome. */
export function getMessageTextDirection(text) {
  return isRtlMarkdown(text) ? 'rtl' : 'ltr'
}

/** Flatten React children (or plain values) to text for per-block direction. */
export function collectNodeText(node) {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(collectNodeText).join('')
  if (typeof node === 'object' && node.props) return collectNodeText(node.props.children)
  return ''
}
