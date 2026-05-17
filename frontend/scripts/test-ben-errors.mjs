/**
 * Node smoke for benErrors humanization (no vitest in frontend).
 * Run: node frontend/scripts/test-ben-errors.mjs
 */
import {
  CLERK_ORG_REQUIRED,
  COUNCIL_PERSISTENCE_FAILED,
  humanizeBenHttpError,
  parseBenErrorResponse,
} from '../src/api/benErrors.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const structured = {
  detail: {
    code: CLERK_ORG_REQUIRED,
    message: 'Please select or create an organization in Clerk to continue.',
    hint: 'Sign out and continue anonymously, or select an organization.',
    recoverable: true,
  },
}

const parsed = parseBenErrorResponse(403, structured)
assert(parsed?.code === CLERK_ORG_REQUIRED, 'structured clerk_org_required')
assert(!humanizeBenHttpError(403, structured).includes('{'), 'no raw JSON in message')

const legacy = { detail: 'Organization context missing from token; select an organization in Clerk.' }
const legacyParsed = parseBenErrorResponse(400, legacy)
assert(legacyParsed?.code === CLERK_ORG_REQUIRED, 'legacy string detail')

const persistFail = {
  error: COUNCIL_PERSISTENCE_FAILED,
  message: 'Council completed but transcript persistence failed. Please retry.',
  retryable: true,
}
const persistParsed = parseBenErrorResponse(503, persistFail)
assert(persistParsed?.code === COUNCIL_PERSISTENCE_FAILED, 'council_persistence_failed code')
assert(
  humanizeBenHttpError(503, persistFail).includes('could not save the transcript'),
  'specific transcript message'
)
assert(
  humanizeBenHttpError(503, persistFail) !== 'Service is temporarily unavailable. Please try again.',
  'not generic 503'
)

console.log('PASS benErrors humanization')
