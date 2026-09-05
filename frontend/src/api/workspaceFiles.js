/**
 * Workspace File Library API client.
 * Must use the same API base as News/Projects (BEN_API_BASE), not a separate env var.
 */
import { BEN_API_BASE } from '../config.js'
import { isPersistedThreadId } from '../threadStorage.js'
import { humanizeBenHttpError, parseBenErrorResponse, readJsonResponse } from './benErrors.js'

function apiBase() {
  return String(BEN_API_BASE || '').replace(/\/$/, '')
}

async function readJson(res) {
  return readJsonResponse(res)
}

function enrichError(res, data) {
  const err = new Error(humanizeBenHttpError(res.status, data))
  err.status = res.status
  err.data = data
  err.parsed = parseBenErrorResponse(res.status, data)
  return err
}

export function workspaceFilesUrl(workspaceId, suffix = '') {
  return `${apiBase()}/api/workspaces/${encodeURIComponent(workspaceId)}/files${suffix}`
}

export async function listWorkspaceFiles(workspaceId, headers, { status, q, limit } = {}) {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  if (q) params.set('q', q)
  if (limit) params.set('limit', String(limit))
  const qs = params.toString()
  const res = await fetch(`${workspaceFilesUrl(workspaceId)}${qs ? `?${qs}` : ''}`, { headers })
  const data = await readJson(res)
  if (!res.ok) throw enrichError(res, data)
  return data
}

export async function uploadWorkspaceFile(
  workspaceId,
  file,
  headers,
  { sourceChatId, onProgress } = {}
) {
  const form = new FormData()
  form.append('file', file, file.name)
  if (isPersistedThreadId(sourceChatId)) form.append('source_chat_id', sourceChatId)

  // XHR for upload progress (fetch lacks reliable upload progress).
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', workspaceFilesUrl(workspaceId))
    Object.entries(headers || {}).forEach(([k, v]) => {
      if (k.toLowerCase() !== 'content-type') xhr.setRequestHeader(k, v)
    })
    xhr.upload.onprogress = (evt) => {
      if (!onProgress) return
      const fileTotal = Number(file?.size) > 0 ? Number(file.size) : null
      const total = evt.lengthComputable && evt.total > 0 ? evt.total : fileTotal
      const loaded = Number.isFinite(evt.loaded) ? evt.loaded : null
      const percent =
        total && loaded != null ? Math.min(100, Math.round((loaded / total) * 100)) : null
      onProgress({ loaded, total, percent })
    }
    xhr.onload = () => {
      let data = {}
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : {}
      } catch {
        data = { detail: xhr.responseText }
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else reject(enrichError({ status: xhr.status, statusText: xhr.statusText }, data))
    }
    xhr.onerror = () => reject(new Error('Network error during upload'))
    xhr.send(form)
  })
}

export async function getWorkspaceFile(workspaceId, fileId, headers, { includeTextPreview } = {}) {
  const qs = includeTextPreview ? '?include_text_preview=true' : ''
  const res = await fetch(workspaceFilesUrl(workspaceId, `/${fileId}${qs}`), { headers })
  const data = await readJson(res)
  if (!res.ok) throw enrichError(res, data)
  return data
}

export function workspaceFileContentUrl(workspaceId, fileId, { inline = true } = {}) {
  return workspaceFilesUrl(
    workspaceId,
    `/${fileId}/content?inline=${inline ? 'true' : 'false'}`
  )
}

export async function deleteWorkspaceFile(workspaceId, fileId, headers) {
  const res = await fetch(workspaceFilesUrl(workspaceId, `/${fileId}`), {
    method: 'DELETE',
    headers,
  })
  const data = await readJson(res)
  if (!res.ok) throw enrichError(res, data)
  return data
}

export async function retryWorkspaceFile(workspaceId, fileId, headers) {
  const res = await fetch(workspaceFilesUrl(workspaceId, `/${fileId}/retry`), {
    method: 'POST',
    headers,
  })
  const data = await readJson(res)
  if (!res.ok) throw enrichError(res, data)
  return data
}

export async function fetchWorkspaceFileBlob(workspaceId, fileId, headers, { inline = true } = {}) {
  const res = await fetch(workspaceFileContentUrl(workspaceId, fileId, { inline }), {
    headers,
  })
  if (!res.ok) {
    const data = await readJson(res)
    throw enrichError(res, data)
  }
  return res.blob()
}
