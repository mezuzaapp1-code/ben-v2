import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse } from './benErrors.js'

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
    const res = await fetch(`${BEN_API_BASE}/chat/stream`, {
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
      const err = new Error(humanizeBenHttpError(res.status, data))
      err.status = res.status
      err.data = data
      throw err
    }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('Chat stream unavailable.')

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
