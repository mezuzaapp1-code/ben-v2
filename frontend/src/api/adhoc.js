import { BEN_API_BASE } from '../config.js'

import { humanizeBenHttpError, parseBenErrorResponse, readJsonResponse } from './benErrors.js'

import { CHAT_STREAM_IDLE_TIMEOUT_MS } from './chat.js'

/** Dedicated ad-hoc expert routes. */
export const ADHOC_EXPERT_PATH = (threadId) =>
  `${BEN_API_BASE}/api/threads/${encodeURIComponent(threadId)}/adhoc/expert`

export const ADHOC_EXPERT_STREAM_PATH = (threadId) =>
  `${BEN_API_BASE}/api/threads/${encodeURIComponent(threadId)}/adhoc/expert/stream`

export const ADHOC_SYNTHESIS_PIPELINE = 'copy_paste'

function enrichFetchError(res, data) {
  const err = new Error(humanizeBenHttpError(res.status, data))
  err.status = res.status
  err.data = data
  err.parsed = parseBenErrorResponse(res.status, data)
  return err
}

export async function postAdhocExpert(threadId, body, headers) {
  const res = await fetch(ADHOC_EXPERT_PATH(threadId), {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

/**
 * POST /api/threads/{id}/adhoc/expert/stream — raw NDJSON token stream.
 */
export async function* postAdhocExpertStream({
  threadId,
  sessionId,
  providerId,
  tier = 'free',
  anchorMessageId = null,
  opinionMode = 'single',
  opinionRequest = null,
  headers,
  signal,
}) {
  const body = {
    session_id: sessionId,
    provider_id: providerId,
    tier,
    anchor_message_id: anchorMessageId ?? undefined,
    opinion_mode: opinionMode,
    opinion_request: opinionRequest ?? undefined,
  }
  const controller = new AbortController()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  let idleTimer = null
  const clearIdleTimer = () => {
    if (idleTimer != null) {
      clearTimeout(idleTimer)
      idleTimer = null
    }
  }
  const resetIdleTimer = () => {
    clearIdleTimer()
    idleTimer = setTimeout(() => controller.abort(), CHAT_STREAM_IDLE_TIMEOUT_MS)
  }

  resetIdleTimer()

  try {
    const res = await fetch(ADHOC_EXPERT_STREAM_PATH(threadId), {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    })

    if (!res.ok) {
      let data = {}
      try {
        data = await res.json()
      } catch {
        data = {}
      }
      throw enrichFetchError(res, data)
    }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('Expert stream unavailable.')

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      resetIdleTimer()
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        resetIdleTimer()
        yield JSON.parse(trimmed)
      }
    }

    const tail = buffer.trim()
    if (tail) {
      resetIdleTimer()
      yield JSON.parse(tail)
    }
  } finally {
    clearIdleTimer()
  }
}
