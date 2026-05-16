import { BEN_API_BASE } from '../config.js'

export async function fetchThreadList(headers) {
  const res = await fetch(`${BEN_API_BASE}/api/threads`, { headers })
  if (!res.ok) throw new Error(`threads list ${res.status}`)
  return res.json()
}

export async function fetchThreadDetail(threadId, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/threads/${encodeURIComponent(threadId)}`, { headers })
  if (!res.ok) throw new Error(`thread ${res.status}`)
  return res.json()
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
