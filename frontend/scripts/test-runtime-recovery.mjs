/**
 * Node smoke for runtime recovery helpers.
 * Run: node frontend/scripts/test-runtime-recovery.mjs
 */
import {
  clearCouncilPending,
  createClientRequestId,
  markCouncilPending,
  recoverStaleCouncilUi,
  resetRuntimeRecoveryForTests,
} from '../src/runtimeRecovery.js'
import { IDEMPOTENCY_REJECTED, parseBenErrorResponse } from '../src/api/benErrors.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

resetRuntimeRecoveryForTests()
const id = createClientRequestId()
assert(id && id.length > 8, 'client request id')

markCouncilPending({ clientRequestId: id, threadId: 't1' })
assert(!recoverStaleCouncilUi(), 'not stale yet')

const parsed = parseBenErrorResponse(409, {
  detail: {
    code: IDEMPOTENCY_REJECTED,
    message: 'in progress',
    recoverable: true,
  },
})
assert(parsed?.code === IDEMPOTENCY_REJECTED, 'parse idempotency rejected')

clearCouncilPending()
console.log('PASS runtime-recovery')
