import { useEffect, useRef, useState } from 'react'
import { withMediaDeadline } from '../api/mediaDeadline.js'
import { BEN_API_BASE } from '../config.js'
import { pendingMedia, clearPendingMedia } from '../api/media.js'

export default function MobileVideoUpload({ workspaceId, scope, buildHeaders, ensureConversation, onAccepted, disabled }) {
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const lock = useRef(false)
  const abort = useRef(null)
  const pendingScope = `mobile:${scope}:${workspaceId}`
  useEffect(() => {
    abort.current = new AbortController()
    return () => abort.current.abort()
  }, [pendingScope])
  async function upload() {
    if (!file || lock.current || disabled) return
    if (!/\.(mp4|mov)$/i.test(file.name) || file.size > 64 * 1024 * 1024) {
      setMessage('Choose an MP4 or MOV file up to 64 MiB.'); return
    }
    lock.current = true; setBusy(true); setMessage('Uploading video…')
    const parentSignal = abort.current.signal
    try {
      await withMediaDeadline(async signal => {
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()))]
        .map(value => value.toString(16).padStart(2, '0')).join('')
      if (signal.aborted) return
      const conversation = await ensureConversation()
      signal.throwIfAborted()
      const saved = pendingMedia(sessionStorage, pendingScope, {
        conversation_id: conversation, workspace_id: workspaceId, checksum: hash,
      })
      if (saved.checksum !== hash) {
        setMessage('A previous upload is awaiting confirmation. Select that same video and retry.'); return
      }
      const query = new URLSearchParams({ conversation_id: saved.conversation_id,
        ...(saved.workspace_id ? { workspace_id: saved.workspace_id } : {}), idempotency_key: saved.idempotency_key })
      const headers = new Headers(await buildHeaders())
      signal.throwIfAborted()
      headers.set('Content-Type', 'application/octet-stream')
      const response = await fetch(`${BEN_API_BASE}/api/media/video-imports?${query}`, {
        method: 'POST', headers, body: file, signal,
      })
      if (signal.aborted) return
      if (!response.ok) {
        if ([400, 401, 403, 404, 409, 413, 422, 429].includes(response.status)) {
          clearPendingMedia(sessionStorage, pendingScope)
          const details = await response.json().catch(() => ({}))
          const messages = {
            VIDEO_DURATION_EXCEEDED: 'Video must be 30 seconds or shorter.',
            VIDEO_SIZE_EXCEEDED: 'Video must be 64 MiB or smaller.',
            VIDEO_UNSUPPORTED_FORMAT: 'This video format or color profile is not supported.',
          }
          setMessage(messages[details.detail?.code] || 'Upload rejected. Check access and video format.')
          return
        }
        throw new Error('unconfirmed')
      }
      clearPendingMedia(sessionStorage, pendingScope)
      setMessage('Video accepted. Conversion progress and playback appear in Conversation media.')
      onAccepted()
      }, parentSignal)
    } catch {
      if (!parentSignal.aborted) setMessage('Upload was not confirmed. Select the same video and retry to recover the saved request.')
    } finally {
      lock.current = false
      if (!parentSignal.aborted) setBusy(false)
    }
  }
  return <section aria-label="Upload video">
    <h2>Upload video</h2>
    <p>MP4 or MOV · H.264 or HEVC · up to 30 seconds, 64 MiB and 4K. Your original is preserved.</p>
    <p>Your video stays in this conversation. No project is required.</p>
    <label>Video file<input type="file" accept=".mp4,.mov,video/mp4,video/quicktime"
      disabled={disabled || busy} onChange={e => setFile(e.target.files?.[0] || null)} /></label>
    <button type="button" disabled={disabled || busy || !file} onClick={() => void upload()}>
      {busy ? 'Uploading…' : 'Upload and prepare video'}</button>
    {message && <p role="status" aria-live="polite">{message}</p>}
  </section>
}
