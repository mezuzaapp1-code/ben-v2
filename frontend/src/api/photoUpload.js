import { BEN_API_BASE } from '../config.js'
import { pendingMedia, clearPendingMedia } from './media.js'
export const validPhoto = file => !!file && /\.(jpe?g|png)$/i.test(file.name) && file.size > 0 && file.size <= 20 * 1024 * 1024

// Selection is local. Only the explicit Generate action uploads the original.
export async function uploadPhoto({ file, conversationId, scope, buildHeaders, signal }) {
  if (!validPhoto(file)) throw new Error('Choose a JPEG or PNG up to 20 MiB.')
  const checksum = [...new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()))].map(v => v.toString(16).padStart(2, '0')).join('')
  signal.throwIfAborted()
  const key = `chat-photo:${scope}:${conversationId}`
  const saved = pendingMedia(sessionStorage, key, { conversation_id: conversationId, checksum })
  if (saved.checksum !== checksum) throw new Error('The previous photo upload was not confirmed. Select that same photo and send again to recover it safely.')
  const query = new URLSearchParams({ conversation_id: saved.conversation_id, idempotency_key: saved.idempotency_key })
  const headers = new Headers(await buildHeaders())
  headers.set('Content-Type', 'application/octet-stream')
  let response
  try { response = await fetch(`${BEN_API_BASE}/api/media/photo-sources?${query}`, {
    method: 'POST', headers, body: file, signal,
  }) } catch (error) {
    throw new Error('Photo upload was not confirmed. Send again with the same photo to recover it safely.', { cause: error })
  }
  if (!response.ok) {
    if ([400, 401, 403, 404, 409, 413, 422].includes(response.status)) {
      clearPendingMedia(sessionStorage, key)
      throw new Error(response.status === 413 || response.status === 422
        ? 'This photo could not be used. Choose a JPEG or PNG up to 20 MiB and 20 megapixels.'
        : 'Photo upload was rejected. Check your account access and try again.')
    }
    throw new Error('Photo upload was not confirmed. Send again with the same photo to recover it safely.')
  }
  const source = await response.json()
  signal.throwIfAborted()
  if (!source.file_id) throw new Error('Photo upload was not confirmed. Try again with the same photo.')
  clearPendingMedia(sessionStorage, key)
  return source
}
