import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse } from './benErrors.js'
import { gateALogSummary, gateAMark } from '../lib/gateATiming.js'

export const CHAT_STREAM_IDLE_TIMEOUT_MS = 300_000

export function humanizeChatFetchError(err) {
  if (err?.name === 'AbortError') {
    return 'Chat stream timed out. You can retry.'
  }
  if (err instanceof TypeError) {
    return 'Network error. Check your connection and try again.'
  }
  return err?.message || 'Chat failed unexpectedly. You can retry.'
}

/**
 * POST /chat/stream — async generator over NDJSON token chunks.
 */
export async function* postChatStream({
  message,
  threadId,
  projectId,
  tier = 'free',
  providerId,
  modelOverride,
  preferredLanguage,
  clientRequestId,
  expertOpinion = false,
  projectSetupBootstrap = false,
  headers,
  signal,
}) {
  const body = { message, tier }
  if (threadId) body.thread_id = threadId
  if (projectId) body.project_id = projectId
  if (providerId) body.provider_id = providerId
  if (modelOverride) body.model_override = modelOverride
  if (preferredLanguage) body.preferred_language = preferredLanguage
  if (clientRequestId) body.client_request_id = clientRequestId
  if (expertOpinion) body.expert_opinion = true
  if (projectSetupBootstrap) body.project_setup_bootstrap = true

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
    gateAMark('F1_fetch', { layer: 'frontend_fetch' })
    const res = await fetch(`${BEN_API_BASE}/chat/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    gateAMark('Fh_headers', { layer: 'frontend_fetch', status: res.status })

    if (!res.ok) {
      let data = {}
      try {
        data = await res.json()
      } catch {
        data = {}
      }
      const err = new Error(humanizeBenHttpError(res.status, data))
      err.status = res.status
      err.data = data
      throw err
    }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('Chat stream unavailable.')

    const decoder = new TextDecoder()
    let buffer = ''
    let sawBody = false
    let sawAnswer = false
    let terminal = 'success'

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (!sawBody && value && value.byteLength) {
          sawBody = true
          gateAMark('Fr_first_body', { layer: 'frontend_stream_reader' })
        }
        resetIdleTimer()
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed) continue
          resetIdleTimer()
          const event = JSON.parse(trimmed)
          if (
            !sawAnswer &&
            event?.type === 'chunk' &&
            String(event.content || '').trim()
          ) {
            sawAnswer = true
            gateAMark('F2_first_answer', { layer: 'frontend_ndjson_parse' })
          }
          if (event?.type === 'error') terminal = 'failure'
          if (event?.type === 'done') terminal = 'success'
          yield event
        }
      }

      const tail = buffer.trim()
      if (tail) {
        resetIdleTimer()
        const event = JSON.parse(tail)
        if (
          !sawAnswer &&
          event?.type === 'chunk' &&
          String(event.content || '').trim()
        ) {
          gateAMark('F2_first_answer', { layer: 'frontend_ndjson_parse' })
        }
        if (event?.type === 'error') terminal = 'failure'
        yield event
      }
      gateAMark('F4_stream_end', { layer: 'frontend_stream_reader', terminal })
      gateALogSummary({ terminal, first_text_event: true })
    } catch (err) {
      gateAMark('F4_stream_end', {
        layer: 'frontend_stream_reader',
        terminal: err?.name === 'AbortError' ? 'cancellation' : 'failure',
      })
      gateALogSummary({
        terminal: err?.name === 'AbortError' ? 'cancellation' : 'failure',
      })
      throw err
    }
  } finally {
    clearIdleTimer()
  }
}
