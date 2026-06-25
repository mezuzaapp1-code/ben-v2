import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse, readJsonResponse } from './benErrors.js'

/** Align with backend stream chunk size (services/repository_store.py). */
export const REPOSITORY_UPLOAD_CHUNK_BYTES = 1024 * 1024

export const REPOSITORY_UPLOAD_MIME_TYPES = Object.freeze([
  'application/pdf',
  'application/epub+zip',
])

async function parseJson(res) {
  try {
    return await readJsonResponse(res)
  } catch {
    return {}
  }
}

function enrichFetchError(res, data) {
  const err = new Error(humanizeBenHttpError(res.status, data))
  err.status = res.status
  err.data = data
  err.parsed = parseBenErrorResponse(res.status, data)
  return err
}

export async function fetchProjectActiveFeatures(projectSlug, headers) {
  const res = await fetch(
    `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/active-features`,
    { headers }
  )
  const data = await parseJson(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export async function fetchProjectRepositories(projectSlug, headers) {
  const res = await fetch(
    `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/repositories`,
    { headers }
  )
  const data = await parseJson(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export async function connectProjectRepository(projectSlug, payload, headers) {
  const res = await fetch(
    `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/repositories/connect`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(payload),
    }
  )
  const data = await parseJson(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export async function toggleProjectRepository(projectSlug, repositoryId, headers) {
  const res = await fetch(
    `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/repositories/${encodeURIComponent(repositoryId)}/toggle`,
    { method: 'POST', headers }
  )
  const data = await parseJson(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

/**
 * Read file sequentially in 1MB slices (client-side chunk window), then POST multipart upload.
 * Progress 0–8%: chunk validation · 8–100%: network ingestion (xhr.upload.onprogress).
 *
 * @param {string} projectSlug
 * @param {number} repositoryId
 * @param {File} file
 * @param {Record<string, string>} headers
 * @param {{ onProgress?: (percent: number) => void, signal?: AbortSignal }} [options]
 */
export async function uploadRepositoryFileChunked(
  projectSlug,
  repositoryId,
  file,
  headers,
  { onProgress, signal } = {}
) {
  const totalBytes = file.size
  if (totalBytes === 0) {
    throw new Error('Empty file cannot be uploaded.')
  }

  let offset = 0
  const chunkSize = REPOSITORY_UPLOAD_CHUNK_BYTES
  const prepWeight = 0.08

  while (offset < totalBytes) {
    if (signal?.aborted) {
      throw new Error('Upload cancelled')
    }
    const end = Math.min(offset + chunkSize, totalBytes)
    await file.slice(offset, end).arrayBuffer()
    offset = end
    const prepRatio = offset / totalBytes
    onProgress?.(Math.min(100, Math.round(prepRatio * prepWeight * 100)))
  }

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const formData = new FormData()
    formData.append('file', file, file.name)
    formData.append('repository_id', String(repositoryId))

    const url = `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/repositories/upload`

    const abortHandler = () => {
      xhr.abort()
      reject(new Error('Upload cancelled'))
    }
    signal?.addEventListener('abort', abortHandler, { once: true })

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return
      const networkRatio = event.loaded / event.total
      const blended = prepWeight + networkRatio * (1 - prepWeight)
      onProgress?.(Math.min(100, Math.round(blended * 100)))
    }

    xhr.onload = () => {
      signal?.removeEventListener('abort', abortHandler)
      let data = {}
      try {
        data = JSON.parse(xhr.responseText || '{}')
      } catch {
        data = {}
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100)
        resolve(data)
        return
      }
      reject(enrichFetchError({ status: xhr.status }, data))
    }

    xhr.onerror = () => {
      signal?.removeEventListener('abort', abortHandler)
      reject(new Error('Network error during repository upload'))
    }

    xhr.onabort = () => {
      signal?.removeEventListener('abort', abortHandler)
      reject(new Error('Upload cancelled'))
    }

    xhr.open('POST', url)
    for (const [key, value] of Object.entries(headers || {})) {
      if (key.toLowerCase() === 'content-type') continue
      xhr.setRequestHeader(key, value)
    }
    xhr.send(formData)
  })
}
