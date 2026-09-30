import { useEffect, useRef, useState } from 'react'
import { BEN_API_BASE } from '../config.js'
import { clearPendingMedia, mediaRequest, pendingMedia } from '../api/media.js'

export default function NarrationPanel({ workspaceId, scope, rows, buildHeaders, ensureConversation, onAccepted, disabled }) {
  const [files, setFiles] = useState([])
  const [selected, setSelected] = useState({ video: '', narration: '', music: '' })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [storageReady, setStorageReady] = useState(false)
  const lock = useRef(false)
  const controller = useRef(null)
  const pendingScope = `narration:${scope}:${workspaceId}`
  const videos = rows.filter(r => r.status === 'succeeded' && r.mime_type === 'video/mp4' && r.resource_id)
  const choose = (field, value) => setSelected(old => ({ ...old, [field]: value }))
  useEffect(() => {
    controller.current = new AbortController()
    try {
      setRecovery(!!sessionStorage.getItem(`ben-media-pending-v1:${pendingScope}`))
      setStorageReady(true)
    } catch { setMessage('Browser storage is unavailable. Enable it before submitting.') }
    return () => controller.current.abort()
  }, [pendingScope])
  useEffect(() => {
    const abort = new AbortController()
    async function load() {
      try {
        const response = await fetch(`${BEN_API_BASE}/api/workspaces/${encodeURIComponent(workspaceId)}/files?limit=100`,
          { headers: await buildHeaders(), signal: abort.signal, cache: 'no-store' })
        if (!response.ok) throw new Error()
        const data = await response.json()
        if (!abort.signal.aborted) setFiles((data.items || []).filter(f => f.media_type === 'audio/wav' && ['uploaded', 'ready'].includes(f.status)))
      } catch { if (!abort.signal.aborted) setMessage('Audio library unavailable. Reopen to retry.') }
    }
    if (workspaceId) void load()
    return () => abort.abort()
  }, [workspaceId, buildHeaders])
  async function upload(file, field) {
    if (!file || lock.current || !workspaceId) return
    if (!/\.wav$/i.test(file.name) || file.size > 6000000) {
      setMessage('Choose a WAV file up to 6 MB.'); return
    }
    lock.current = true; setBusy(true); setMessage('Uploading audio...')
    const signal = controller.current.signal
    try {
      const headers = new Headers(await buildHeaders()); headers.delete('Content-Type')
      const form = new FormData(); form.append('file', file)
      const response = await fetch(`${BEN_API_BASE}/api/workspaces/${encodeURIComponent(workspaceId)}/files`,
        { method: 'POST', headers, body: form, signal })
      if (!response.ok) throw new Error()
      const asset = await response.json()
      if (signal.aborted) return
      setFiles(old => [...old.filter(f => f.id !== asset.id), asset])
      choose(field, asset.id); setMessage('Audio uploaded. Format and duration will be checked before rendering.')
    } catch { if (!signal.aborted) setMessage('Upload was not confirmed. Check the file library before trying again.') }
    finally { lock.current = false; if (!signal.aborted) setBusy(false) }
  }
  async function submit() {
    if (lock.current || disabled || !storageReady) return
    lock.current = true; setBusy(true); setMessage('Submitting...')
    const signal = controller.current.signal
    try {
      const conversation = await ensureConversation()
      if (signal.aborted) return
      const body = pendingMedia(sessionStorage, pendingScope, {
        conversation_id: conversation, workspace_id: workspaceId,
        video_resource_id: selected.video, music_file_id: selected.music, narration_file_id: selected.narration,
      })
      setRecovery(true)
      await mediaRequest('/narration-replacements', await buildHeaders(), { body, signal })
      clearPendingMedia(sessionStorage, pendingScope)
      if (signal.aborted) return
      setRecovery(false); setMessage('Replacement accepted. Progress and the finished video appear in Conversation media.')
      onAccepted()
    } catch (error) {
      if (signal.aborted) return
      if ([400, 401, 403, 404, 422, 429].includes(error.status)) {
        clearPendingMedia(sessionStorage, pendingScope); setRecovery(false)
        setMessage(error.status === 422
          ? 'Inputs rejected. Use PCM 16-bit, 48 kHz WAV audio; narration must not exceed the video duration.'
          : 'Request rejected. Check access to the selected conversation and files.')
      } else setMessage('Submission was not confirmed. Retry the saved request to avoid a duplicate.')
    } finally { lock.current = false; if (!signal.aborted) setBusy(false) }
  }
  const blocked = disabled || busy || recovery || !workspaceId
  return <section aria-label="Replace narration" style={{ padding: 16, border: '1px solid #555', borderRadius: 12 }}>
    <h2>Replace narration</h2>
    <p>Keep the original picture. Mix new narration with background music into a separate video.</p>
    {!workspaceId && <p role="status">Open a project workspace to select or upload audio.</p>}
    {videos.length === 0 && <p role="status">No ready BEN video in this conversation. Open a conversation containing one, or use Upload video if it is enabled for your account.</p>}
    <label>Source video<select value={selected.video} disabled={blocked} onChange={e => choose('video', e.target.value)}>
      <option value="">Choose a BEN video</option>
      {videos.map(r => <option key={r.resource_id} value={r.resource_id}>{r.model} - {r.created_at}</option>)}
    </select></label>
    {['narration', 'music'].map(field => <div key={field}>
      <label>{field === 'music' ? 'Background music' : 'Narration'}<select value={selected[field]} disabled={blocked} onChange={e => choose(field, e.target.value)}>
        <option value="">Choose audio</option>
        {files.map(f => <option key={f.id} value={f.id}>{f.original_filename || f.original_name || f.filename || f.id}</option>)}
      </select></label>
      <label>Upload {field}<input type="file" accept=".wav,audio/wav" disabled={blocked}
        onChange={e => { void upload(e.target.files?.[0], field); e.target.value = '' }} /></label>
    </div>)}
    <p>WAV / PCM 16-bit / 48 kHz / up to 6 MB and 30 seconds. Narration must fit within the video.</p>
    {recovery && <p>The saved submission is locked for recovery. Retrying uses its original selections.</p>}
    <button type="button" disabled={disabled || busy || !storageReady || !workspaceId || (!recovery && !(selected.video && selected.narration && selected.music))}
      onClick={() => { void submit() }}>{busy ? 'Working...' : recovery ? 'Retry saved request' : 'Replace narration'}</button>
    {message && <p role="status" aria-live="polite">{message}</p>}
  </section>
}
