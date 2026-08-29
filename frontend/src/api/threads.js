import { humanizeBenHttpError, parseBenErrorResponse, readJsonResponse } from './benErrors.js'
import { BEN_API_BASE } from '../config.js'
import { sanitizeUsedFiles, unavailableChatNote } from '../lib/fileStatus.js'

function enrichFetchError(res, data) {
  const err = new Error(humanizeBenHttpError(res.status, data))
  err.status = res.status
  err.data = data
  err.parsed = parseBenErrorResponse(res.status, data)
  return err
}

/**
 * @param {Record<string, string>} headers
 * @param {{ projectSlug?: string | null, title?: string | null }} [options]
 */
export async function createProjectWorkspace(headers, { projectSlug, title } = {}) {
  /** @type {Record<string, string>} */
  const body = {}
  const slug = String(projectSlug || '').trim()
  const threadTitle = String(title || '').trim()
  if (slug) body.project_slug = slug
  if (threadTitle) body.title = threadTitle

  const res = await fetch(`${BEN_API_BASE}/api/threads/project-workspace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
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

export async function deleteThread(threadId, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/threads/${encodeURIComponent(threadId)}`, {
    method: 'DELETE',
    headers,
  })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export async function promoteThread(threadId, projectSlug, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/threads/${encodeURIComponent(threadId)}/promote`, {
    method: 'POST',
    headers: {
      ...headers,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ project_slug: projectSlug }),
  })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export function mapApiMessage(m) {
  const base = {
    role: m.role,
    content: m.content ?? '',
    model_used: m.model_used ?? '',
    provider_id: m.provider_id ?? '',
    provider_used: m.provider_used ?? '',
    cost_usd: m.cost_usd ?? 0,
    expert_outcome: m.expert_outcome,
    expert_status: m.expert_status,
    kind: m.kind,
    parts: Array.isArray(m.parts) ? m.parts : undefined,
    synthesis: m.synthesis,
    adhoc_session_id: m.adhoc_session_id ?? '',
    output_locale: m.output_locale ?? m.synthesis?.output_locale ?? '',
    synthesis_pipeline: m.synthesis_pipeline ?? m.synthesis?.synthesis_pipeline ?? '',
    sequence: m.sequence,
    sqlite_message_id: m.sqlite_message_id ?? null,
    message_type: m.message_type ?? 'normal',
    insert_after_id: m.insert_after_id ?? null,
    used_files: sanitizeUsedFiles(m.used_files),
    workspace_files_unavailable_note: unavailableChatNote(m.unavailable_count),
    source_event: m.source_event,
    source_file_id: m.source_file_id,
  }
  return base
}

export function mapThreadFromList(t) {
  return {
    id: t.id,
    title: t.title || 'Conversation',
    messages: [],
    loaded: false,
    sessionType: t.session_type || 'chat',
    projectSlug: t.project_slug || null,
  }
}
