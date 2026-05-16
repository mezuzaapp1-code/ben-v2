import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse } from './benErrors.js'

export const COUNCIL_CLIENT_TIMEOUT_MS = 35_000

const COUNCIL_LABEL = {
  'Legal Advisor': '⚖️ Legal Advisor',
  'Business Advisor': '💼 Business Advisor',
  'Strategy Advisor': '🎯 Strategy Advisor',
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
    return 'Council timed out. You can retry.'
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
export async function postCouncil({ question, threadId, clientRequestId, headers, signal }) {
  const body = { question }
  if (threadId) body.thread_id = threadId
  if (clientRequestId) body.client_request_id = clientRequestId
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
  return { res, data }
}
