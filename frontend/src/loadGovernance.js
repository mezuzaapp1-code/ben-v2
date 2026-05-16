/** Client-side load governance: duplicate council guard and request fingerprinting. */

const COUNCIL_DEDUP_MS = 2500

let councilInFlight = false
let lastCouncilFingerprint = ''
let lastCouncilSubmitAt = 0

export function councilRequestFingerprint(question, threadId) {
  const q = (question || '').trim().toLowerCase().replace(/\s+/g, ' ')
  return `${threadId || 'draft'}:${q}`
}

export function canSubmitCouncil(question, threadId) {
  if (councilInFlight) {
    return { ok: false, reason: 'in_flight' }
  }
  const fp = councilRequestFingerprint(question, threadId)
  const now = Date.now()
  if (fp === lastCouncilFingerprint && now - lastCouncilSubmitAt < COUNCIL_DEDUP_MS) {
    return { ok: false, reason: 'duplicate' }
  }
  return { ok: true, fingerprint: fp }
}

export function markCouncilSubmitStarted(fingerprint) {
  councilInFlight = true
  lastCouncilFingerprint = fingerprint
  lastCouncilSubmitAt = Date.now()
}

export function markCouncilSubmitFinished() {
  councilInFlight = false
}

/** Reset for tests. */
export function resetCouncilSubmitGuard() {
  councilInFlight = false
  lastCouncilFingerprint = ''
  lastCouncilSubmitAt = 0
}

export const OVERLOAD_CODES = new Set([
  'council_busy',
  'runtime_saturated',
  'retry_later',
  'duplicate_request',
])
