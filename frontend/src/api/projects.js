import { BEN_API_BASE } from '../config.js'
import { readJsonResponse } from './benErrors.js'

const PROJECTS_BASE = `${BEN_API_BASE}/api/projects`

async function projectFetch(path, { method = 'GET', headers, body, signal } = {}) {
  const res = await fetch(`${PROJECTS_BASE}${path}`, {
    method,
    headers,
    body: body != null ? JSON.stringify(body) : undefined,
    signal,
  })
  const data = await readJsonResponse(res)
  if (!res.ok) {
    const err = new Error(data?.detail || data?.message || `Project API ${res.status}`)
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export async function fetchProjects(headers, signal) {
  return projectFetch('', { headers, signal })
}

export async function createProject(payload, headers, signal) {
  return projectFetch('', {
    method: 'POST',
    headers,
    body: payload,
    signal,
  })
}

/**
 * JIT conversational onboarding — provisions Postgres project + project_context.db schema.
 * @param {import('../lib/conversationalInitPayload.js').ConversationalInitRequestBody} payload
 */
export async function conversationalProjectInit(payload, headers, signal) {
  return projectFetch('/conversational-init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: payload,
    signal,
  })
}

export async function fetchProject(projectId, headers, signal) {
  return projectFetch(`/${projectId}`, { headers, signal })
}

export async function fetchProjectLedger(projectId, headers, signal) {
  return projectFetch(`/${projectId}/ledger`, { headers, signal })
}

export async function captureInvoice(projectId, payload, headers, signal) {
  return projectFetch(`/${projectId}/invoices/capture`, {
    method: 'POST',
    headers,
    body: payload,
    signal,
  })
}

export async function captureCreditMemo(projectId, payload, headers, signal) {
  return projectFetch(`/${projectId}/credit-memos/capture`, {
    method: 'POST',
    headers,
    body: payload,
    signal,
  })
}

export async function exportLedger(projectId, { format = 'summary' } = {}, headers, signal) {
  return projectFetch(`/${projectId}/ledger/export`, {
    method: 'POST',
    headers,
    body: { format },
    signal,
  })
}

export async function listNativeTools(projectId, headers, signal) {
  return projectFetch(`/${projectId}/tools`, { headers, signal })
}

export async function executeNativeTool(projectId, toolName, arguments_, headers, signal) {
  return projectFetch(`/${projectId}/tools/execute`, {
    method: 'POST',
    headers,
    body: { tool_name: toolName, arguments: arguments_ || {} },
    signal,
  })
}
