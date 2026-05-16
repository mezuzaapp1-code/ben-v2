/**
 * Council progressive UX unit checks.
 * Run: node frontend/scripts/test-council-progress.mjs
 */
import {
  COUNCIL_PHASE_ORDER,
  COUNCIL_TIMEOUT_USER_MESSAGE,
  isLongCouncilPrompt,
  isRtlPreferredText,
  sanitizeCouncilErrorMessage,
} from '../src/councilProgress.js'
function humanizeFetchErrorLike(err) {
  if (err?.name === 'AbortError') return COUNCIL_TIMEOUT_USER_MESSAGE
  const msg = String(err?.message ?? '')
  if (/ReadTimeout|timeout/i.test(msg)) return COUNCIL_TIMEOUT_USER_MESSAGE
  return sanitizeCouncilErrorMessage(msg)
}

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

assert(COUNCIL_PHASE_ORDER.length === 5, 'five phases')
assert(COUNCIL_PHASE_ORDER[0] === 'started', 'started first')
assert(COUNCIL_PHASE_ORDER.includes('legal'), 'legal phase')
assert(COUNCIL_PHASE_ORDER.includes('synthesizing'), 'synthesizing phase')

assert(
  sanitizeCouncilErrorMessage("error: ReadTimeout('')") === COUNCIL_TIMEOUT_USER_MESSAGE,
  'ReadTimeout sanitized'
)
assert(humanizeFetchErrorLike({ name: 'AbortError' }) === COUNCIL_TIMEOUT_USER_MESSAGE, 'abort message')
assert(
  humanizeFetchErrorLike(new Error("ReadTimeout('')")) === COUNCIL_TIMEOUT_USER_MESSAGE,
  'ReadTimeout error'
)
assert(!sanitizeCouncilErrorMessage('').includes('ReadTimeout'), 'empty')

assert(isLongCouncilPrompt('x'.repeat(500)), 'long chars')
assert(!isLongCouncilPrompt('short'), 'short')
assert(isRtlPreferredText('שלום עולם'), 'hebrew rtl')
assert(!isRtlPreferredText('Hello world'), 'english ltr')

console.log('PASS council progressive UX checks')
