/**
 * Node smoke for benErrors humanization (no vitest in frontend).
 * Run: node frontend/scripts/test-ben-errors.mjs
 */
import {
  CLERK_ORG_REQUIRED,
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

console.log('PASS benErrors humanization')
