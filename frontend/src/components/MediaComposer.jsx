import { useEffect, useRef, useState } from 'react'
import { ComposerCapsule } from './ComposerCapsule.jsx'
import { clearPendingMedia, mediaRequest, mediaTerminal, pendingMedia } from '../api/media.js'

function MediaImage({ resourceId, buildHeaders }) {
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl
    async function load() {
      try {
        const bytes = await mediaRequest(`/resources/${resourceId}/content`, await buildHeaders(),
          { binary: true, signal: controller.signal })
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(bytes)
        setUrl(objectUrl)
      } catch {
        if (!controller.signal.aborted) setError(true)
      }
    }
    void load()
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [resourceId, buildHeaders])
  return url ? <a href={url} download="ben-image.png"><img src={url} alt="BEN generated image" style={{ maxWidth: '100%', maxHeight: 360 }} /></a>
    : <p>{error ? 'Image unavailable. Reopen to retry.' : 'Loading image…'}</p>
}

/** Text child is the unchanged BEN composer; media has its own explicit path. */
export default function MediaComposer({ children, conversationId, scope, buildHeaders, ensureConversation, disabled }) {
  const [enabled, setEnabled] = useState(false)
  const [models, setModels] = useState(['gemini-3.1-flash-image'])
  const [model, setModel] = useState('gemini-3.1-flash-image')
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
          setEnabled(caps.image === true)
          setModels(caps.models)
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
        conversation_id: id, model, prompt, aspect_ratio: ratio,
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
  if (!enabled || !buildHeaders) return children
  return <>
    <div aria-label="Composer mode">
      <button type="button" disabled={busy} aria-pressed={mode === 'text'} onClick={() => setMode('text')}>Text</button>
      <button type="button" disabled={busy} aria-pressed={mode === 'image'} onClick={() => setMode('image')}>Image · internal</button>
    </div>
    {rows.length > 0 && <section aria-label="Conversation images" aria-live="polite">
      {rows.map(row => <article key={row.execution_id}>
        <p>{row.provider} · {row.model} · {row.status.replaceAll('_', ' ')}</p>
        {row.status === 'submission_unknown' && <p>Provider outcome unknown. BEN will not resubmit.</p>}
        {row.error_code && <p>{row.error_code.replaceAll('_', ' ')}</p>}
        {row.resource_id && <MediaImage resourceId={row.resource_id} buildHeaders={buildHeaders} />}
      </article>)}
    </section>}
    {mode === 'text' ? children : <>
      <label>Engine <select value={model} disabled={busy} onChange={e => setModel(e.target.value)}>
        {models.map(value => <option key={value} value={value}>{value === 'flux-2-pro' ? 'BFL FLUX.2 Pro' : 'Google Gemini 3.1 Flash Image'}</option>)}
      </select></label>
      <label>Aspect ratio <select value={ratio} disabled={busy} onChange={e => setRatio(e.target.value)}>
        {['1:1', '16:9', '9:16'].map(value => <option key={value}>{value}</option>)}
      </select></label>
      <ComposerCapsule value={prompt} onChange={setPrompt} onSubmit={submit}
        disabled={disabled || busy} loading={busy} canSend={!!prompt.trim() && !busy}
        placeholder="Describe an image" ariaLabel="Image prompt" sendLabel="Generate image" />
      {error && <p role="alert">{error}</p>}
    </>}
  </>
}
