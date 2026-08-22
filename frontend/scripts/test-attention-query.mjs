/**
 * Active Context Focus bounded retrieval query.
 * Run: node frontend/scripts/test-attention-query.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { humanizeBenHttpError, parseBenErrorResponse } from '../src/api/benErrors.js'
import { createActiveFocusController } from '../src/lib/activeFocusSession.js'
import {
  ATTENTION_QUERY_CLIENT_MAX_CHARS,
  ATTENTION_QUERY_ELLIPSIS,
  ATTENTION_QUERY_MAX_ENCODED_BYTES,
  ATTENTION_QUERY_SERVER_MAX_CHARS,
  buildAttentionQuery,
  splitComposerQueries,
} from '../src/lib/attentionQuery.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function encodedBytes(query) {
  return new URLSearchParams({ query }).toString().length
}

function assertBounded(query, label) {
  const points = Array.from(query)
  assert(points.length <= ATTENTION_QUERY_SERVER_MAX_CHARS, `${label}: under server decoded cap`)
  assert(encodedBytes(query) <= ATTENTION_QUERY_MAX_ENCODED_BYTES, `${label}: under encoded cap`)
}

// 1. 100-char message unchanged
{
  const short = 'x'.repeat(100)
  assert(short.length === 100, '100-char fixture')
  assert(buildAttentionQuery(short) === short, '100-char query unchanged')
}

// 2. exactly-safe-bound message succeeds unchanged (ASCII)
{
  const exact = 'a'.repeat(ATTENTION_QUERY_CLIENT_MAX_CHARS)
  assert(buildAttentionQuery(exact) === exact, 'exact client bound unchanged')
}

// 3. 5,000-char message never exceeds server/encoded ceilings
{
  const long = 'b'.repeat(5000)
  const q = buildAttentionQuery(long)
  assert(q !== long, '5000-char is bounded')
  assertBounded(q, '5000-char')
  assert(q.includes(ATTENTION_QUERY_ELLIPSIS), '5000-char uses head+tail marker')
}

// 4. 20,000-char paste with the real question at the END — tail survives
{
  const question = 'What is the width of the opening in the Data Hall?'
  const pasted = `${'PASTE-DOC-HEAD\n'.repeat(800)}${'z'.repeat(12000)}\n${question}`
  assert(pasted.length > 20000, 'architecture-sized paste')
  const q = buildAttentionQuery(pasted)
  assertBounded(q, '20k paste')
  assert(q.includes(question), 'ending request survives in Focus query')
  assert(q.includes('PASTE-DOC-HEAD'), 'beginning context also survives')
}

// 5. important context at beginning still present
{
  const head = 'SHEET TLV62-BWE-P3-GF-DR-S-1001 SCALE 1:150'
  const tail = 'List every fire-rated opening.'
  const msg = `${head}\n${'n'.repeat(8000)}\n${tail}`
  const q = buildAttentionQuery(msg)
  assert(q.includes('TLV62-BWE-P3-GF-DR-S-1001') || q.includes(head.slice(0, 20)), 'head context survives')
  assert(q.includes(tail), 'tail request survives')
}

// 6. Hebrew long message — no broken encoding, tail question survives
{
  const question = 'מה הרוחב של הפתח באולם הנתונים?'
  const body = `${'שלום עולם '.repeat(400)}\n${question}`
  const q = buildAttentionQuery(body)
  assertBounded(q, 'hebrew')
  assert(q.includes(question), 'hebrew ending request survives')
  assert(!q.includes('\uFFFD'), 'hebrew has no replacement chars')
}

// 7. emoji / multi-byte — bounded on code points, tail survives
{
  const tail = 'MEASURE_THE_OPENING'
  const msg = `${'😀📐'.repeat(1500)}${tail}`
  const q = buildAttentionQuery(msg)
  assertBounded(q, 'emoji')
  assert(q.includes(tail), 'emoji paste keeps ending request')
}

// 8. chat still receives FULL original message
{
  const full = `${'BEGIN_CONTEXT\n'.repeat(100)}${'m'.repeat(18000)}\nEND_QUESTION_PLEASE`
  const split = splitComposerQueries(full)
  assert(split.chatMessage === full, 'chat message is the original full string')
  assert(split.attentionQuery.length < full.length, 'focus query is bounded')
  assert(split.attentionQuery.includes('END_QUESTION_PLEASE'), 'focus tail kept')
}

// 9–11. shared composer path (gpt/claude/gemini) — source lock
{
  const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
  assert(app.includes('message: encoded'), 'chat send uses encoded current-turn content')
  assert(app.includes('focusSourceFromParts'), 'Focus query is instruction/stub, not the paste body')
  assert(app.includes('setAttentionFocusRequest'), 'focus request is separate')
  assert(app.includes('for await (const event of postChatStream'), 'single composer stream for all engines')
  assert(app.includes('providerId: activeSpeakingProviderId'), 'engine chip selects provider; query builder is shared')
  const knowledge = readFileSync(join(root, 'src/api/knowledge.js'), 'utf8')
  assert(knowledge.includes('buildAttentionQuery'), 'Focus GET bounds query at API boundary')
  assert(knowledge.includes('new URLSearchParams({ query: boundedQuery })'), 'bounded query is what is encoded')
}

// 12. Focus 422/failure does not retry forever and does not live on the chat path
{
  const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
  assert(app.includes('never gate /chat/stream'), 'send comments Focus as auxiliary')
  const session = readFileSync(join(root, 'src/lib/activeFocusSession.js'), 'utf8')
  assert(session.includes('e?.status === 401'), 'Focus retries only after 401')
  assert(!session.includes('status === 422'), 'Focus does not retry 422')
}

// 13. FastAPI validation-array 422 is not "check your session" for string_too_long
{
  const tooLong = {
    detail: [
      {
        type: 'string_too_long',
        loc: ['query', 'query'],
        msg: 'String should have at most 4096 characters',
        input: 'x'.repeat(5000),
      },
    ],
  }
  const parsed = parseBenErrorResponse(422, tooLong)
  assert(parsed?.message === 'Context Focus query was too long.', 'string_too_long query copy')
  assert(!parsed.message.includes('session'), 'does not blame session')
  assert(!humanizeBenHttpError(422, tooLong).includes('Check your session'), 'humanize matches')

  const stringDetail = parseBenErrorResponse(422, { detail: 'Invalid project_id' })
  assert(stringDetail?.message === 'Invalid project_id', 'string detail preserved')

  const extra = parseBenErrorResponse(422, {
    detail: [{ type: 'extra_forbidden', loc: ['body', 'nope'], msg: 'Extra inputs are not permitted' }],
  })
  assert(extra?.message === 'Invalid request. Check your session and try again.', 'other 422 arrays keep generic copy')
}

assert(ATTENTION_QUERY_SERVER_MAX_CHARS === 4096, 'server cap matches FastAPI')
assert(ATTENTION_QUERY_CLIENT_MAX_CHARS < ATTENTION_QUERY_SERVER_MAX_CHARS, 'client budget below server')

await (async () => {
  let fetches = 0
  const ctrl = createActiveFocusController({
    acquireHeaders: async () => ({ Authorization: 'Bearer test' }),
    fetchFocus: async () => {
      fetches += 1
      const err = new Error('Context Focus query was too long.')
      err.status = 422
      throw err
    },
    retryDelayMs: 15,
  })
  ctrl.start({ projectSlug: 'demo', threadId: 'thread-1', query: 'q'.repeat(5000) })
  await sleep(80)
  assert(fetches === 1, 'Focus 422 is not retried')
  assert(ctrl.getSnapshot().error.includes('too long'), 'Focus error stays local')
  ctrl.stop()
})()

console.log('PASS attention query + focus isolation + 422 UX')
