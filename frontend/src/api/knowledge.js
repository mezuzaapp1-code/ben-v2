import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse } from './benErrors.js'

async function parseJson(res) {
  try {
    return await res.json()
  } catch {
    return {}
  }
}

export async function fetchKnowledgeBases(headers) {
  const res = await fetch(`${BEN_API_BASE}/api/knowledge/bases`, { headers })
  const data = await parseJson(res)
  if (!res.ok) {
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export async function createKnowledgeBase(name, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/knowledge/bases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ name }),
  })
  const data = await parseJson(res)
  if (!res.ok) {
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export async function deleteKnowledgeBase(baseId, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/knowledge/bases/${baseId}`, {
    method: 'DELETE',
    headers,
  })
  if (!res.ok) {
    const data = await parseJson(res)
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    throw err
  }
}

export async function fetchKnowledgeDocuments(baseId, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/knowledge/bases/${baseId}/documents`, { headers })
  const data = await parseJson(res)
  if (!res.ok) {
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    throw err
  }
  return data
}

export async function addKnowledgeDocument(baseId, { title, content }, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/knowledge/bases/${baseId}/documents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ title, content }),
  })
  const data = await parseJson(res)
  if (!res.ok) {
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export async function deleteKnowledgeDocument(docId, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/knowledge/documents/${docId}`, {
    method: 'DELETE',
    headers,
  })
  if (!res.ok) {
    const data = await parseJson(res)
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    throw err
  }
}

export async function fetchProjectKnowledgeFiles(projectSlug, headers) {
  const res = await fetch(
    `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/knowledge/files`,
    { headers }
  )
  const data = await parseJson(res)
  if (!res.ok) {
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export async function fetchActiveAttention(projectSlug, threadId, query, headers) {
  const params = new URLSearchParams({ query })
  const res = await fetch(
    `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/threads/${encodeURIComponent(threadId)}/active-attention?${params}`,
    { headers }
  )
  const data = await parseJson(res)
  if (!res.ok) {
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}
