/**
 * Bounded retrieval query for Active Context Focus.
 *
 * Chat keeps the full user message. Focus is a separate GET ?query= contract
 * with a server-side decoded-character ceiling (FastAPI Query max_length).
 *
 * Keep ATTENTION_QUERY_SERVER_MAX_CHARS in sync with
 * routers/knowledge.py ACTIVE_ATTENTION_QUERY_MAX_CHARS.
 */

/** FastAPI decoded-character ceiling on GET active-attention. */
export const ATTENTION_QUERY_SERVER_MAX_CHARS = 4096

/**
 * Client decoded-character budget. Stays below the server cap so the
 * head+tail query plus percent-encoding (Hebrew/emoji) fits typical
 * 8 KiB request-line proxies. A second encoded-byte clamp shrinks further.
 */
export const ATTENTION_QUERY_CLIENT_MAX_CHARS = 1800

/** Percent-encoded `query=<value>` ceiling, including the `query=` prefix. */
export const ATTENTION_QUERY_MAX_ENCODED_BYTES = 3600

export const ATTENTION_QUERY_ELLIPSIS = '\n...\n'

/** Share of the inner budget given to the beginning/context portion. */
export const ATTENTION_QUERY_HEAD_RATIO = 0.4

const MIN_CODEPOINTS = 32

function codePoints(text) {
  return Array.from(text ?? '')
}

function encodedQueryBytes(query) {
  return new URLSearchParams({ query }).toString().length
}

function assembleHeadTail(points, maxChars) {
  const sep = codePoints(ATTENTION_QUERY_ELLIPSIS)
  if (points.length <= maxChars) {
    return points.join('')
  }
  const inner = Math.max(2, maxChars - sep.length)
  let headN = Math.max(1, Math.floor(inner * ATTENTION_QUERY_HEAD_RATIO))
  let tailN = Math.max(1, inner - headN)
  if (headN + tailN >= points.length) {
    return points.join('')
  }
  return points.slice(0, headN).join('') + ATTENTION_QUERY_ELLIPSIS + points.slice(-tailN).join('')
}

/**
 * Build a retrieval query from the full composer message.
 * Short messages are unchanged. Long messages keep a beginning slice and
 * an ending slice so a trailing user question still reaches Focus.
 */
export function buildAttentionQuery(fullMessage, maxChars = ATTENTION_QUERY_CLIENT_MAX_CHARS) {
  const text = String(fullMessage ?? '').trim()
  if (!text) return ''

  const points = codePoints(text)
  const ceiling = Math.max(MIN_CODEPOINTS, Math.min(Number(maxChars) || ATTENTION_QUERY_CLIENT_MAX_CHARS, ATTENTION_QUERY_SERVER_MAX_CHARS))

  if (points.length <= ceiling && encodedQueryBytes(text) <= ATTENTION_QUERY_MAX_ENCODED_BYTES) {
    return text
  }

  let budget = Math.min(ceiling, points.length)
  let query = assembleHeadTail(points, budget)
  while (encodedQueryBytes(query) > ATTENTION_QUERY_MAX_ENCODED_BYTES && budget > MIN_CODEPOINTS) {
    budget = Math.max(MIN_CODEPOINTS, Math.floor(budget * 0.85))
    query = assembleHeadTail(points, budget)
  }
  if (encodedQueryBytes(query) > ATTENTION_QUERY_MAX_ENCODED_BYTES) {
    query = assembleHeadTail(points, MIN_CODEPOINTS)
  }
  return query
}

/** Chat path: full original message. Focus path: bounded retrieval query. */
export function splitComposerQueries(fullMessage) {
  const chatMessage = String(fullMessage ?? '')
  return {
    chatMessage,
    attentionQuery: buildAttentionQuery(chatMessage),
  }
}
