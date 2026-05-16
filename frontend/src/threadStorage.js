const ACTIVE_THREAD_KEY = 'ben_active_thread_id'

export function getStoredActiveThreadId() {
  try {
    return localStorage.getItem(ACTIVE_THREAD_KEY)
  } catch {
    return null
  }
}

export function setStoredActiveThreadId(threadId) {
  try {
    if (threadId) localStorage.setItem(ACTIVE_THREAD_KEY, threadId)
    else localStorage.removeItem(ACTIVE_THREAD_KEY)
  } catch {
    /* ignore */
  }
}

export const DRAFT_PREFIX = 'draft:'

export function isDraftThreadId(id) {
  return typeof id === 'string' && id.startsWith(DRAFT_PREFIX)
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

/** Server-persisted thread id (safe to send as thread_id). */
export function isPersistedThreadId(id) {
  return typeof id === 'string' && UUID_RE.test(id)
}

export function serverThreadIdForApi(activeId) {
  if (!activeId || isDraftThreadId(activeId)) return undefined
  if (isPersistedThreadId(activeId)) return activeId
  return undefined
}
