import { BEN_API_BASE } from '../config.js'

export async function mediaRequest(path, headers, { body, signal, binary = false } = {}) {
  const response = await fetch(`${BEN_API_BASE}/api/media${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { ...headers, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined, signal, cache: 'no-store',
  })
  if (!response.ok) {
    const error = new Error(`Media request failed (${response.status}).`)
    error.status = response.status
    throw error
  }
  return binary ? response.blob() : response.json()
}

export const mediaTerminal = status => ['succeeded', 'failed', 'expired'].includes(status)

// Persist intent/key before POST; a lost reply or reload must reuse that key.
// If browser storage is unavailable, fail before any generation is submitted.
export function pendingMedia(storage, scope, intent) {
  const key = `ben-media-pending-v1:${scope}`
  const existing = storage.getItem(key)
  if (existing) return JSON.parse(existing)
  const body = { ...intent, idempotency_key: crypto.randomUUID() }
  storage.setItem(key, JSON.stringify(body))
  return body
}

export function clearPendingMedia(storage, scope) {
  storage.removeItem(`ben-media-pending-v1:${scope}`)
}
