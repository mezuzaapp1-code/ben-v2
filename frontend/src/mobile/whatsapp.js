/**
 * Compliant https://wa.me/ deep links — opens native WhatsApp client (iOS/Android/PWA).
 * Never use api.whatsapp.com or window.postMessage; anchor navigation avoids cross-origin blocks.
 */

export function normalizeWhatsAppPhone(raw) {
  if (!raw) return ''
  return String(raw).replace(/\D/g, '')
}

/**
 * @param {{ phone?: string, message?: string }} opts
 * @returns {string} Absolute wa.me URL
 */
export function buildWhatsAppUrl({ phone, message } = {}) {
  const digits = normalizeWhatsAppPhone(phone)
  const text = (message || '').trim()
  const query = text ? `?text=${encodeURIComponent(text)}` : ''
  if (digits) {
    return `https://wa.me/${digits}${query}`
  }
  return `https://wa.me/${query}`
}

export function openWhatsAppDeepLink(opts) {
  const url = buildWhatsAppUrl(opts)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.target = '_blank'
  anchor.rel = 'noopener noreferrer'
  anchor.setAttribute('referrerpolicy', 'no-referrer')
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
