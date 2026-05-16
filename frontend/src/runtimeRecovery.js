/** Client request idempotency + refresh recovery for council lifecycle. */

const PENDING_COUNCIL_KEY = 'ben:council-pending'
const STALE_PENDING_MS = 40_000

export function createClientRequestId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return `ben-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function markCouncilPending({ clientRequestId, threadId }) {
  try {
    sessionStorage.setItem(
      PENDING_COUNCIL_KEY,
      JSON.stringify({
        clientRequestId,
        threadId: threadId || null,
        at: Date.now(),
      })
    )
  } catch {
    /* ignore quota */
  }
}

export function clearCouncilPending() {
  try {
    sessionStorage.removeItem(PENDING_COUNCIL_KEY)
  } catch {
    /* ignore */
  }
}

/**
 * Clear stale loading UI after refresh during an abandoned council submit.
 * @returns {boolean} true if stale state was recovered
 */
export function recoverStaleCouncilUi() {
  if (typeof sessionStorage === 'undefined') {
    return false
  }
  try {
    const raw = sessionStorage.getItem(PENDING_COUNCIL_KEY)
    if (!raw) return false
    const data = JSON.parse(raw)
    if (!data?.at || Date.now() - data.at < STALE_PENDING_MS) {
      return false
    }
    clearCouncilPending()
    return true
  } catch {
    clearCouncilPending()
    return true
  }
}

export function resetRuntimeRecoveryForTests() {
  clearCouncilPending()
}
