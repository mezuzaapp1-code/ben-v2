/**
 * Lightweight /news path helpers (History API — no React Router in this app).
 */

/**
 * @typedef {{ view: 'feed', eventId: null } | { view: 'detail', eventId: string }} NewsRoute
 */

/**
 * @param {string} [pathname]
 * @returns {NewsRoute | null}
 */
export function parseNewsLocation(pathname = typeof window !== 'undefined' ? window.location.pathname : '/') {
  const path = String(pathname || '/').split('?')[0].split('#')[0]
  if (path === '/news' || path === '/news/') {
    return { view: 'feed', eventId: null }
  }
  const match = path.match(/^\/news\/([^/]+)\/?$/)
  if (!match) return null
  const eventId = decodeURIComponent(match[1] || '').trim()
  if (!eventId) return { view: 'feed', eventId: null }
  return { view: 'detail', eventId }
}

export function newsFeedPath() {
  return '/news'
}

/** @param {string} eventId */
export function newsTopicPath(eventId) {
  return `/news/${encodeURIComponent(String(eventId || '').trim())}`
}

/** @param {string} eventId */
export function isValidEventId(eventId) {
  const value = String(eventId || '').trim()
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}
