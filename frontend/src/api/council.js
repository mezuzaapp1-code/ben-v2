import { BEN_API_BASE } from '../config.js'
import {
  buildSynthesisBubbleText,
  expertStatusLabel,
  labelLocale,
  t,
} from '../cognitiveLabels.js'
import { detectDominantLanguage } from '../languageContext.js'
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

export function humanizeCouncilFetchError(err, dominantLanguage = 'en') {
  const locale = labelLocale(dominantLanguage)
  if (err?.name === 'AbortError') {
    return t(locale, 'council_timeout')
  }
  if (err instanceof TypeError) {
    return t(locale, 'council_network')
  }
  return t(locale, 'council_failed')
}

/**
 * @param {object} data - /council JSON body
 * @param {(s: object, failed: boolean) => string} synthesisTextFn
 */
export function councilResponseToMessages(data, synthesisTextFn) {
  const members = Array.isArray(data.council) ? data.council : []
  const syn = data.synthesis && typeof data.synthesis === 'object' ? data.synthesis : null
  const anyExpertFailed = members.some((c) => c.outcome && c.outcome !== 'ok')
  const lang = data.dominant_language || detectDominantLanguage(data.question || '').dominant_language
  const dir = data.text_direction || detectDominantLanguage(data.question || '').text_direction
  const locale = labelLocale(lang)
  const meta = { dominant_language: lang, text_direction: dir }
  const textFn =
    synthesisTextFn ||
    ((s, failed) => buildSynthesisBubbleText(s, failed, locale))
  const messages = members.map((c, i) => {
    const name = c.expert || 'Advisor'
    const head = COUNCIL_LABEL[name] || name
    const lastExpert = i === members.length - 1 && !syn
    const statusLabel =
      c.outcome && c.outcome !== 'ok'
        ? expertStatusLabel(locale, c.outcome, c.response)
        : null
    return {
      role: 'assistant',
      content: `${head}: ${c.response ?? ''}`,
      model_used: c.model ?? '',
      expert_outcome: c.outcome ?? 'ok',
      expert_status: statusLabel,
      cost_usd: lastExpert ? data.cost_usd ?? 0 : 0,
      ...meta,
    }
  })
  if (syn) {
    messages.push({
      role: 'assistant',
      kind: 'council_synthesis',
      synthesis: syn,
      content: textFn(syn, anyExpertFailed),
      model_used: 'synthesis',
      cost_usd: data.cost_usd ?? 0,
      ...meta,
    })
  }
  if (messages.length === 0) {
    messages.push({
      role: 'assistant',
      kind: 'council_error',
      content: t(locale, 'council_empty'),
      model_used: '',
      cost_usd: 0,
      ...meta,
    })
  }
  return messages
}

/**
 * POST /council with AbortController timeout.
 */
export async function postCouncil({ question, threadId, headers, signal }) {
  const body = { question }
  if (threadId) body.thread_id = threadId
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
