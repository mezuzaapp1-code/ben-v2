import PhotoSourceUpload from './PhotoSourceUpload.jsx'
import MediaImage from './MediaAsset.jsx'
import { cloneElement, isValidElement, useEffect, useRef, useState } from 'react'
import CreativeEditLab from './CreativeEditLab.jsx'
import NarrationPanel from './NarrationPanel.jsx'
import MobileVideoUpload from './MobileVideoUpload.jsx'
import { ComposerCapsule } from './ComposerCapsule.jsx'
import { clearPendingMedia, mediaRequest, mediaTerminal, pendingMedia } from '../api/media.js'


function SavedWork({ scope, buildHeaders }) {
  const [items, setItems] = useState(null), [error, setError] = useState('')
  const [selected, setSelected] = useState(null), [editing, setEditing] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const result = await mediaRequest('/edit-documents', await buildHeaders(), { signal: controller.signal })
        if (!controller.signal.aborted) setItems(result.documents)
      } catch { if (!controller.signal.aborted) setError('Saved work could not be loaded. Close and reopen to retry.') }
    }
    void load()
    return () => controller.abort()
  }, [buildHeaders, scope])
  return <section className="ben-saved-work" aria-label="My saved work">
    <h3>My saved work</h3><p>Private edits saved to your BEN account.</p>
    {error && <p role="alert">{error}</p>}
    {!items && !error && <p role="status">Loading saved work…</p>}
    {items?.length === 0 && <p>Save your first video edit to find it here.</p>}
    {items?.map((item, index) => <button type="button" key={item.document_id} onClick={() => { setSelected(item); setEditing(true) }}>
      Video edit {items.length - index} · version {item.head_number} · {new Date(item.updated_at).toLocaleString()}
    </button>)}
    {selected && <MediaImage key={`${scope}:${selected.document_id}`} resourceId={selected.resource_id} documentId={selected.document_id}
      mimeType="video/mp4" scope={scope} savedEditing buildHeaders={buildHeaders} editing={editing} onEdit={() => setEditing(true)} onClose={() => setEditing(false)} />}
  </section>
}

/** Text child is the unchanged BEN composer; media has its own explicit path. */
export default function MediaComposer({ children, conversationId, scope, buildHeaders, ensureConversation, disabled, workspaceId, onChooseProject, onCreateProject, selectedPhoto, onOpenSavedWork }) {
  const [photoSource, setPhotoSource] = useState(null)
  useEffect(() => { if (selectedPhoto) { setMode('video'); setPhotoSource(null) } }, [selectedPhoto])
  useEffect(() => setPhotoSource(null), [scope, workspaceId, conversationId])
  const [editingId, setEditingId] = useState(null)
  const [mobileEnabled, setMobileEnabled] = useState(false)
  const [savedEditing, setSavedEditing] = useState(false), [workOpen, setWorkOpen] = useState(false)
  const [labEnabled, setLabEnabled] = useState(false)
  const [narrationEnabled, setNarrationEnabled] = useState(false)
  const [labOpen, setLabOpen] = useState(false)
  const [enabled, setEnabled] = useState(false)
  const [models, setModels] = useState(['gemini-3.1-flash-image'])
  const [model, setModel] = useState('gemini-3.1-flash-image')
  const [videoModels, setVideoModels] = useState([])
  const [videoModel, setVideoModel] = useState('')
  const [videoParameters, setVideoParameters] = useState({})
  const videoChoice = videoModels.includes(videoModel) ? videoModel : videoModels[0]
  const videoSettings = videoParameters[videoChoice] || { duration_seconds: 4, resolution: '720p', audio: 'native' }
  const [sourceId, setSourceId] = useState('')
  const [videoRatio, setVideoRatio] = useState('16:9')
  const [mode, setMode] = useState('text')
  const [prompt, setPrompt] = useState('')
  const [ratio, setRatio] = useState('1:1')
  const [history, setHistory] = useState({ conversationId: null, rows: [] })
  const rows = history.conversationId === conversationId ? history.rows : []
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const sending = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    async function check() {
      try {
        const caps = await mediaRequest('/capabilities', await buildHeaders(), { signal: controller.signal })
        if (!controller.signal.aborted) {
          setLabEnabled(caps.creative_lab === true)
          setNarrationEnabled(caps.narration_replacement === true)
          setMobileEnabled(caps.mobile_video_import === true)
          setSavedEditing(caps.edit_documents === true)
          setEnabled(caps.image === true)
          setModels(caps.models)
          setVideoModels(caps.video_models || [])
          setVideoParameters(caps.video_model_parameters || {})
        }
      } catch { if (!controller.signal.aborted) setEnabled(false) }
    }
    if (buildHeaders) void check()
    return () => controller.abort()
  }, [buildHeaders, scope])
  useEffect(() => {
    const controller = new AbortController()
    let timer
    async function reload() {
      try {
        const result = await mediaRequest(`/executions?conversation_id=${encodeURIComponent(conversationId)}`,
          await buildHeaders(), { signal: controller.signal })
        if (controller.signal.aborted) return
        setHistory({ conversationId, rows: result.executions })
        if (result.executions.some(row => !mediaTerminal(row.status))) timer = setTimeout(reload, 2000)
      } catch { if (!controller.signal.aborted) setError('Media history unavailable. Reopen to retry.') }
    }
    if (enabled && conversationId) void reload()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [enabled, conversationId, buildHeaders, refresh])
  async function submit() {
    if (sending.current || disabled) return
    sending.current = true
    setBusy(true)
    setError('')
    let pendingScope
    try {
      const id = await ensureConversation()
      pendingScope = `${scope}:${id}`
      const body = pendingMedia(sessionStorage, pendingScope, {
        conversation_id: id, prompt,
        ...(mode === 'video' ? { model: videoChoice, ...(photoSource ? { source_file_id: photoSource.file_id, workspace_id: photoSource.workspace_id } : { source_resource_id: sourceId }),
          aspect_ratio: videoRatio, duration_seconds: videoSettings.duration_seconds, resolution: videoSettings.resolution }
          : { model, aspect_ratio: ratio }),
      })
      await mediaRequest('/executions', await buildHeaders(), { body })
      clearPendingMedia(sessionStorage, pendingScope)
      setPrompt('')
      setRefresh(value => value + 1)
    } catch (failure) {
      if ([400, 401, 403, 404, 422, 429].includes(failure.status) && pendingScope) {
        clearPendingMedia(sessionStorage, pendingScope)
        setError(failure.message)
      } else {
        setError('Submission was not confirmed. Send again to recover the same saved request; it will not generate a duplicate.')
      }
    } finally { sending.current = false; setBusy(false) }
  }
  if (!enabled || !buildHeaders || (selectedPhoto && !videoModels.length)) return <>{children}{selectedPhoto && <p role="status">Your photo is selected. Media tools are unavailable for this account right now.</p>}</>
  const actions = [
    ...(children?.props?.attachMenuItems || []),
    ...(mobileEnabled ? [{ id: 'video-upload', label: 'Upload video', icon: '▷', disabled: disabled || busy, onClick: () => setMode('upload') }] : []),
    ...(rows.some(row => row.resource_id && row.mime_type === 'video/mp4') ? [{ id: 'video-edit', label: 'Edit video', icon: '✎', disabled: disabled || busy, onClick: () => setEditingId(rows.find(row => row.resource_id && row.mime_type === 'video/mp4').resource_id) }] : []),
    ...(savedEditing ? [{ id: 'saved-work', label: 'My saved work', icon: '▣', disabled: disabled || busy, onClick: () => onOpenSavedWork ? onOpenSavedWork() : setWorkOpen(true) }] : []),
    ...(narrationEnabled ? [{ id: 'narration', label: 'Replace narration', icon: '♫', disabled: disabled || busy, onClick: () => setMode('narration') }] : []),
    { id: 'image', label: 'Generate image', icon: '◇', disabled: disabled || busy, onClick: () => setMode('image') },
    ...(videoModels.length ? [{ id: 'video', label: 'Animate my photo', icon: '▷', disabled: disabled || busy, onClick: () => setMode('video') }] : []),
  ]
  const composer = isValidElement(children) ? cloneElement(children, { attachMenuItems: actions }) : children
  return <>
    {savedEditing && <button type="button" aria-expanded={workOpen} onClick={() => onOpenSavedWork ? onOpenSavedWork() : setWorkOpen(v => !v)}>{workOpen ? 'Close saved work' : 'My saved work'}</button>}
    {savedEditing && workOpen && <SavedWork key={scope} scope={scope} buildHeaders={buildHeaders} />}
    {labEnabled && <div>
      <button type="button" aria-expanded={labOpen} onClick={() => setLabOpen(open => !open)}>Creative Edit Lab - internal</button>
      {labOpen && <CreativeEditLab key={`${scope}:${workspaceId}`} workspaceId={workspaceId} buildHeaders={buildHeaders} />}
    </div>}
    {mode !== 'text' && <button type="button" disabled={busy} onClick={() => setMode('text')}>Back to message</button>}
    {rows.length > 0 && <section aria-label="Conversation media" aria-live="polite">
      {rows.map(row => <article key={row.execution_id}>
        <p>{row.provider} Â· {row.model} Â· {row.status.replaceAll('_', ' ')}</p>
        {row.status === 'submission_unknown' && <p>Provider outcome unknown. BEN will not resubmit.</p>}
        {row.error_code && <p>{row.error_code.replaceAll('_', ' ')}</p>}
        {row.resource_id && <MediaImage key={`${scope}:${row.resource_id}`} scope={scope} savedEditing={savedEditing} resourceId={row.resource_id} buildHeaders={buildHeaders} mimeType={row.mime_type} editing={editingId === row.resource_id} onEdit={() => setEditingId(row.resource_id)} onClose={() => setEditingId(null)} />}
      </article>)}
    </section>}
    {mode === 'text' ? composer : mode === 'upload' ? <MobileVideoUpload
      onChooseProject={onChooseProject} onCreateProject={onCreateProject}
      key={`${scope}:${workspaceId}`} scope={scope} workspaceId={workspaceId}
      buildHeaders={buildHeaders} ensureConversation={ensureConversation} disabled={disabled}
      onAccepted={() => setRefresh(value => value + 1)} /> : mode === 'narration' ? <NarrationPanel
      key={`${scope}:${workspaceId}:${conversationId}`} scope={`${scope}:${conversationId || 'new'}`}
      workspaceId={workspaceId} rows={rows} buildHeaders={buildHeaders} ensureConversation={ensureConversation}
      disabled={disabled} onAccepted={() => setRefresh(value => value + 1)} /> : <>
      {mode === 'video' ? <>
        <PhotoSourceUpload key={`${scope}:${workspaceId}`} workspaceId={workspaceId} scope={scope}
          initialFile={selectedPhoto} buildHeaders={buildHeaders} ensureConversation={ensureConversation} disabled={disabled || busy || !videoModels.length}
          onChooseProject={onChooseProject} onCreateProject={onCreateProject} onReady={setPhotoSource} />
        <p>Generate video sends this image and your instructions to the selected AI provider and may incur charges.</p>
        <details><summary>Video settings</summary>
        <label>Video engine <select value={videoChoice} disabled={busy} onChange={e => setVideoModel(e.target.value)}>
          {videoModels.map(value => <option key={value} value={value}>{value === 'veo-3.1-fast-generate-preview' ? 'Google Veo 3.1 Fast' : 'Kling O3 Standard via fal'}</option>)}
        </select></label>
        <p>{videoSettings.duration_seconds} seconds · {videoSettings.resolution} · audio {videoSettings.audio}</p>
        {videoSettings.source_aspect_ratio_required && <p>Choose an image matching the selected aspect ratio, at least 300 pixels on each side.</p>}
        <label>First frame <select value={sourceId} disabled={busy} onChange={e => { setSourceId(e.target.value); setPhotoSource(null) }}>
          <option value="">{photoSource ? "Your uploaded photo is selected" : "Or choose a BEN image"}</option>
          {rows.filter(row => row.resource_id && row.mime_type === 'image/png').map(row =>
            <option key={row.resource_id} value={row.resource_id}>{row.model} · {row.created_at}</option>)}
        </select></label>
        <label>Aspect ratio <select value={videoRatio} disabled={busy} onChange={e => setVideoRatio(e.target.value)}>
          {['16:9', '9:16'].map(value => <option key={value}>{value}</option>)}
        </select></label>
        </details>
      </> : <>
      <label>Engine <select value={model} disabled={busy} onChange={e => setModel(e.target.value)}>
        {models.map(value => <option key={value} value={value}>{value === 'flux-2-pro' ? 'BFL FLUX.2 Pro' : 'Google Gemini 3.1 Flash Image'}</option>)}
      </select></label>
      <label>Aspect ratio <select value={ratio} disabled={busy} onChange={e => setRatio(e.target.value)}>
        {['1:1', '16:9', '9:16'].map(value => <option key={value}>{value}</option>)}
      </select></label>
      </>}
      <ComposerCapsule value={prompt} onChange={setPrompt} onSubmit={submit}
        disabled={disabled || busy} loading={busy} canSend={!!prompt.trim() && !busy && (mode !== 'video' || photoSource || rows.some(row => row.resource_id === sourceId && row.mime_type === 'image/png'))}
        placeholder={mode === 'video' ? 'Describe motion for the selected image' : 'Describe an image'}
        ariaLabel={mode === 'video' ? 'Video prompt' : 'Image prompt'} sendLabel={mode === 'video' ? 'Generate video' : 'Generate image'} />
      {error && <p role="alert">{error}</p>}
    </>}
  </>
}
