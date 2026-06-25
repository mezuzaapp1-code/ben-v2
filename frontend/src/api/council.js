import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse } from './benErrors.js'

/** Generous ceiling for council streams (5 minutes idle without bytes/events). */
export const COUNCIL_STREAM_IDLE_TIMEOUT_MS = 300_000

/** @deprecated Use COUNCIL_STREAM_IDLE_TIMEOUT_MS for stream paths. */
export const COUNCIL_CLIENT_TIMEOUT_MS = 35_000

/** Non-stream POST /council JSON requests. */
export const COUNCIL_REQUEST_TIMEOUT_MS = 90_000

const COUNCIL_LABEL = {
  'Legal Advisor': '⚖️ Legal Advisor',
  'Business Advisor': '💼 Business Advisor',
  'Strategy Advisor': '🎯 Strategy Advisor',
  'Local Codebase Expert': '🧩 Local Codebase Expert',
}

export function humanizeCouncilHttpError(status, data) {
  const parsed = parseBenErrorResponse(status, data)
  if (parsed) return parsed.message
  const detail = data?.detail
  if (status === 429 || status === 503) {
    if (typeof detail === 'object' && detail?.message) return String(detail.message)
  }
  if (status === 401) {
    return typeof detail === 'string' ? detail : 'Sign in required to use Council.'
  }
  if (status === 400) {
    if (typeof detail === 'string') return detail
    return 'Organization context missing. Select an organization in Clerk and try again.'
  }
  if (status === 422) {
    if (typeof detail === 'string') return detail
    return 'Invalid request. Check your session and try again.'
  }
  if (status >= 500) {
    return 'Council is temporarily unavailable. Please try again in a moment.'
  }
  if (status === 0) {
    return 'Could not reach the server. Check your connection and try again.'
  }
  return `Council request failed (${status}). You can retry.`
}

export function humanizeCouncilFetchError(err) {
  if (err?.name === 'AbortError') {
    const idleMin = Math.round(COUNCIL_STREAM_IDLE_TIMEOUT_MS / 60_000)
    return `Council stream timed out after ${idleMin} minutes without activity. You can retry.`
  }
  if (err instanceof TypeError) {
    return 'Network error. Check your connection and try again.'
  }
  return 'Council failed unexpectedly. You can retry.'
}

/**
 * @param {object} data - /council JSON body
 * @param {(s: object, failed: boolean) => string} synthesisTextFn
 */
export function councilResponseToMessages(data, synthesisTextFn) {
  const members = Array.isArray(data.council) ? data.council : []
  const syn = data.synthesis && typeof data.synthesis === 'object' ? data.synthesis : null
  const anyExpertFailed = members.some((c) => c.outcome && c.outcome !== 'ok')
  const messages = members.map((c, i) => {
    const name = c.expert || 'Advisor'
    const head = COUNCIL_LABEL[name] || name
    const lastExpert = i === members.length - 1 && !syn
    let statusLabel = null
    if (c.outcome && c.outcome !== 'ok') {
      if (c.outcome === 'timeout') statusLabel = 'Unavailable: timeout'
      else if (c.outcome === 'degraded') {
        const m = /Expert unavailable \(([^)]+)\)/.exec(c.response || '')
        statusLabel = m ? `Degraded: ${m[1]}` : 'Degraded'
      } else statusLabel = `Degraded: ${c.outcome}`
    }
    return {
      role: 'assistant',
      content: `${head}: ${c.response ?? ''}`,
      model_used: c.model ?? '',
      expert_outcome: c.outcome ?? 'ok',
      expert_status: statusLabel,
      cost_usd: lastExpert ? data.cost_usd ?? 0 : 0,
    }
  })
  if (syn) {
    messages.push({
      role: 'assistant',
      kind: 'council_synthesis',
      synthesis: syn,
      content: synthesisTextFn(syn, anyExpertFailed),
      model_used: 'synthesis',
      cost_usd: data.cost_usd ?? 0,
    })
  }
  if (messages.length === 0) {
    messages.push({
      role: 'assistant',
      kind: 'council_error',
      content: 'Council returned no responses. You can retry.',
      model_used: '',
      cost_usd: 0,
    })
  }
  return messages
}

/**
 * POST /council with AbortController timeout.
 */
export async function postCouncil({ question, threadId, clientRequestId, headers, signal, forceCodebase = false }) {
  const body = { question }
  if (threadId) body.thread_id = threadId
  if (clientRequestId) body.client_request_id = clientRequestId
  if (forceCodebase) body.force_codebase = true
  const url = `${BEN_API_BASE}/council`
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })
  let data = {}
  try {
    data = await res.json()
  } catch {
    data = {}
  }
  console.log('[ben.next_steps]', data?.synthesis?.next_steps ?? data?.next_steps)
  return { res, data }
}

/**
 * POST /council/stream — async generator over NDJSON events.
 * @param {object} opts
 * @param {string} opts.question
 * @param {string} [opts.threadId]
 * @param {string} [opts.clientRequestId]
 * @param {HeadersInit} opts.headers — from buildBenHeaders(getToken)
 * @param {AbortSignal} [opts.signal]
 * @yields {object} Parsed NDJSON event (e.g. { type: 'expert' | 'synthesis' | 'error', ... })
 */
export async function* postCouncilStream({
  question,
  threadId,
  clientRequestId,
  headers,
  signal,
  forceCodebase = false,
}) {
  const body = { question }
  if (threadId) body.thread_id = threadId
  if (clientRequestId) body.client_request_id = clientRequestId
  if (forceCodebase) body.force_codebase = true

  const controller = new AbortController()
  if (signal) {
    if (signal.aborted) {
      controller.abort()
    } else {
      signal.addEventListener('abort', () => controller.abort(), { once: true })
    }
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
    idleTimer = setTimeout(() => controller.abort(), COUNCIL_STREAM_IDLE_TIMEOUT_MS)
  }

  resetIdleTimer()

  try {
    const res = await fetch(`${BEN_API_BASE}/council/stream`, {
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
      const err = new Error(humanizeCouncilHttpError(res.status, data))
      err.status = res.status
      err.data = data
      throw err
    }

    const reader = res.body?.getReader()
    if (!reader) {
      throw new Error('Council stream unavailable. You can retry.')
    }

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
