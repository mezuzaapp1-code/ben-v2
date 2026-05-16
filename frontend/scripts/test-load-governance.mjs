/**
 * Node smoke for load governance client helpers.
 * Run: node frontend/scripts/test-load-governance.mjs
 */
import {
  canSubmitCouncil,
  markCouncilSubmitFinished,
  markCouncilSubmitStarted,
  resetCouncilSubmitGuard,
} from '../src/loadGovernance.js'
import { DUPLICATE_REQUEST, parseBenErrorResponse } from '../src/api/benErrors.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

resetCouncilSubmitGuard()
assert(canSubmitCouncil('Q?', 't1').ok, 'first submit ok')
markCouncilSubmitStarted('t1:q?')
assert(!canSubmitCouncil('Q?', 't1').ok, 'in flight blocked')
assert(canSubmitCouncil('Q?', 't1').reason === 'in_flight', 'in flight reason')
markCouncilSubmitFinished()

resetCouncilSubmitGuard()
markCouncilSubmitStarted('t1:hello')
markCouncilSubmitFinished()
assert(!canSubmitCouncil('hello', 't1').ok, 'rapid duplicate blocked')

const overload = parseBenErrorResponse(503, {
  detail: {
    code: DUPLICATE_REQUEST,
    message: 'Already running',
    hint: 'Wait',
    recoverable: true,
  },
})
assert(overload?.code === DUPLICATE_REQUEST, 'parse overload code')

console.log('PASS load-governance')
