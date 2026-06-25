import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse, readJsonResponse } from './benErrors.js'

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

export async function fetchPlatformActiveFeatures(headers) {
  const res = await fetch(`${BEN_API_BASE}/api/platform/active-features`, { headers })
  const data = await parseJson(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export async function connectPlatformCapability(payload, headers) {
  const res = await fetch(`${BEN_API_BASE}/api/platform/capabilities/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(payload),
  })
  const data = await parseJson(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

export async function togglePlatformCapability(channelId, headers) {
  const res = await fetch(
    `${BEN_API_BASE}/api/platform/capabilities/${encodeURIComponent(channelId)}/toggle`,
    { method: 'POST', headers }
  )
  const data = await parseJson(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}
