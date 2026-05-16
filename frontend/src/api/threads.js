import { humanizeBenHttpError, parseBenErrorResponse, readJsonResponse } from './benErrors.js'
import { BEN_API_BASE } from '../config.js'

function enrichFetchError(res, data) {
  const err = new Error(humanizeBenHttpError(res.status, data))
  err.status = res.status
  err.data = data
  err.parsed = parseBenErrorResponse(res.status, data)
  return err
}

export async function fetchThreadList(headers) {
  const res = await fetch(`${BEN_API_BASE}/api/threads`, { headers })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export async function fetchThreadDetail(threadId, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/threads/${encodeURIComponent(threadId)}`, { headers })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export function mapApiMessage(m) {
  const base = {
    role: m.role,
    content: m.content ?? '',
    model_used: m.model_used ?? '',
    cost_usd: m.cost_usd ?? 0,
    expert_outcome: m.expert_outcome,
    expert_status: m.expert_status,
    kind: m.kind,
    synthesis: m.synthesis,
  }
  return base
}

export function mapThreadFromList(t) {
  return { id: t.id, title: t.title || 'Conversation', messages: [], loaded: false }
}
