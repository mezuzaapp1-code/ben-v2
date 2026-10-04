import { withMediaDeadline } from '../api/mediaDeadline.js'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { activeSubtitle, DEFAULT_STYLE, parseSrt, toSrt, validateCues, subtitleDirection, subtitleAlignment, clampSubtitlePoint } from '../lib/subtitleDraft.js'
import './VideoSubtitleEditor.css'
import { subtitleStyleCommand } from '../lib/subtitleCommands.js'
import FreeTextLayer from './FreeTextLayer.jsx'
import TextAppearanceControls from './TextAppearanceControls.jsx'
import { textAppearance, textBackground } from '../lib/textAppearance.js'
import { readEditorDraft, writeEditorDraft, checkedDraft } from '../lib/editorStorage.js'
import { editFailure } from '../lib/editSession.js'

export default function VideoSubtitleEditor(props) {
  return <EditorSession key={props.draftKey || props.url} {...props} />
}
function EditorSession({ url, open, onClose, initialCues = [], draftKey, remote, onExport }) {
  const [exportStatus, setExportStatus] = useState(''), [exportUrl, setExportUrl] = useState(''), [exportBusy, setExportBusy] = useState(false)
  const exportController = useRef(null)
  useEffect(() => () => exportController.current?.abort(), [])
  useEffect(() => () => { if (exportUrl) URL.revokeObjectURL(exportUrl) }, [exportUrl])
  const [loaded] = useState(() => remote ? { saved: null, error: '' } : readEditorDraft(draftKey))
  const [draft, setDraft] = useState(() => loaded.saved?.draft || { cues: initialCues, style: DEFAULT_STYLE, textLayers: [] })
  const [savedSnapshot, setSavedSnapshot] = useState(() => loaded.saved ? JSON.stringify(loaded.saved.draft) : null)
  const [saveError, setSaveError] = useState(loaded.error)
  const savedDuration = useRef(loaded.saved?.duration)
  const [past, setPast] = useState([]), [future, setFuture] = useState([])
  const [expanded, setExpanded] = useState(true), [selected, setSelected] = useState('')
  const [time, setTime] = useState(0), [duration, setDuration] = useState(0)
  const [cloud, setCloud] = useState({ loading: !!remote, saving: false, revision: null, pending: false, ready: !remote })
  const [versions, setVersions] = useState(null)
  const [historyBusy, setHistoryBusy] = useState(false)
  const saving = useRef(false), initialized = useRef(null), alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!open || !remote || !duration || initialized.current === remote) return undefined
    let cancelled = false
    remote.open(duration).then(result => {
      if (cancelled) return
      initialized.current = remote
      if (result.draft) setDraft(result.draft)
      setSavedSnapshot(result.draft && !result.pending ? JSON.stringify(result.draft) : null)
      setCloud({ loading: false, saving: false, ready: true, revision: result.revision, pending: !!result.pending })
      setSaveError(result.pending ? 'An earlier save was not confirmed. Retry save to recover it before editing.' : '')
    }).catch(error => {
      if (!cancelled) { setCloud(c => ({ ...c, loading: false, ready: false })); setSaveError(error.code === 'EDIT_DURATION' ? error.message : 'Saved edits could not be loaded. Close and reopen the editor to retry. Nothing has been overwritten.') }
    })
    return () => { cancelled = true }
  }, [remote, duration, open])
  const [error, setError] = useState(''), [caption, setCaption] = useState('')
  const [start, setStart] = useState('0'), [end, setEnd] = useState('2')
  const [playing, setPlaying] = useState(false)
  const [audio, setAudio] = useState({ muted: false, volume: 1 })
  const [command, setCommand] = useState(''), [commandStatus, setCommandStatus] = useState('')
  const [ratio, setRatio] = useState(16 / 9)
  const [dragPoint, setDragPoint] = useState(null)
  const drag = useRef(null), captionBox = useRef(null)
  const [selectedText, setSelectedText] = useState('')
  const dialog = useRef(null), video = useRef(null), close = useRef(null), request = useRef(0)
  const closeAction = useRef(onClose)
  const editedCue = draft.cues.find(cue => cue.id === selected)
  const pendingText = editedCue ? caption !== editedCue.text || Number(start) !== editedCue.start || Number(end) !== editedCue.end : !!caption.trim()
  const dirty = pendingText || JSON.stringify(draft) !== savedSnapshot
  const actions = useRef(null)
  useEffect(() => { actions.current = { saveDraft, undo, redo } })
  useEffect(() => {
    if (!open || !dirty) return undefined
    const warn = e => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [open, dirty])
  async function exportMp4() {
    if (exportController.current || dirty || cloud.saving || cloud.pending || !onExport) return
    const controller = new AbortController(); exportController.current = controller
    const timer = setTimeout(() => controller.abort(), 180000)
    setExportBusy(true); setExportUrl(''); setExportStatus('Preparing text…')
    try {
      const blob = await withMediaDeadline(signal => onExport(video.current.videoWidth, video.current.videoHeight, signal, setExportStatus), controller.signal, 180000)
      if (!alive.current || controller.signal.aborted) return
      setExportUrl(URL.createObjectURL(blob)); setExportStatus(`MP4 ready · version ${cloud.revision}`)
    } catch (e) {
      if (alive.current) setExportStatus(controller.signal.aborted ? 'Export timed out. Your saved edit is safe; please retry.' : e.message)
    } finally {
      clearTimeout(timer); exportController.current = null
      if (alive.current) setExportBusy(false)
    }
  }
  function requestClose() {
    if (cloud.saving) return
    if (remote && dirty && !cloud.pending && !window.confirm('Close with unsaved changes? Keep this video open to continue, or save first.')) return
    onClose()
  }
  useEffect(() => { closeAction.current = requestClose })
  useEffect(() => {
    if (!open) return undefined
    const pendingRequests = request
    const previous = document.activeElement, overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'; close.current?.focus()
    function keyboard(e) {
      if (e.ctrlKey || e.metaKey) {
        const key = e.key.toLowerCase()
        if (key === 's') { e.preventDefault(); actions.current?.saveDraft(); return }
        const typing = e.target?.matches?.('input,textarea,[contenteditable="true"]')
        if (!typing && (key === 'z' || key === 'y')) {
          e.preventDefault(); actions.current?.[key === 'y' || e.shiftKey ? 'redo' : 'undo'](); return
        }
      }
      if (e.key === 'Escape') { e.preventDefault(); closeAction.current() }
      if (e.key !== 'Tab') return
      const items = [...dialog.current.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select,textarea,video[controls],[tabindex="0"]')].filter(el => el.getClientRects().length)
      const first = items[0], last = items.at(-1)
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keyboard)
    return () => { pendingRequests.current++; document.removeEventListener('keydown', keyboard); document.body.style.overflow = overflow; previous?.focus?.() }
  }, [open])
  function commit(next) {
    if (remote && (!cloud.ready || cloud.saving || cloud.pending)) return
    setPast(h => [...h.slice(-29), draft]); setFuture([]); setDraft(next)
  }
  function resetSelection() { setSelected(''); setCaption('') }
  function undo() {
    if (remote && (!cloud.ready || cloud.saving || cloud.pending)) return
    if (!past.length) return
    setFuture(h => [draft,...h]); setDraft(past.at(-1)); setPast(h => h.slice(0,-1)); resetSelection()
  }
  function redo() {
    if (remote && (!cloud.ready || cloud.saving || cloud.pending)) return
    if (!future.length) return
    setPast(h => [...h.slice(-29),draft]); setDraft(future[0]); setFuture(h => h.slice(1)); resetSelection()
  }
  function withPendingText() {
    if (!pendingText) return draft
    const cue = { id: selected || crypto.randomUUID(), text: caption, start: Number(start), end: Number(end) }
    const cues = selected ? draft.cues.map(c => c.id === selected ? cue : c) : [...draft.cues,cue]
    validateCues(cues,duration)
    return { ...draft, cues }
  }
  async function saveDraft() {
    if (saving.current || (remote && !cloud.ready)) return
    try {
      const next = checkedDraft(withPendingText(), duration)
      saving.current = true
      if (remote) {
        setCloud(c => ({ ...c, saving: true }))
        const result = await remote.save(next)
        if (!alive.current) return
        setDraft(result.draft); setSavedSnapshot(JSON.stringify(result.draft))
        setCloud(c => ({ ...c, revision: result.revision, pending: false })); setVersions(null)
        setSaveError(''); resetSelection(); return
      }
      writeEditorDraft(draftKey, next, duration)
      if (JSON.stringify(next) !== JSON.stringify(draft)) commit(next)
      setSavedSnapshot(JSON.stringify(next)); setSaveError(''); resetSelection()
    } catch (e) {
      if (alive.current) {
        setSaveError(remote ? editFailure(e) : e.message)
        if (remote) setCloud(c => ({ ...c, pending: !!remote.pending }))
      }
    } finally {
      saving.current = false
      if (alive.current) setCloud(c => ({ ...c, saving: false }))
    }
  }
  async function loadVersion(id) {
    if (saving.current || cloud.pending) return
    try {
      // Include unapplied caption typing in Undo before switching versions.
      const previous = withPendingText()
      saving.current = true; setCloud(c => ({ ...c, saving: true }))
      const result = id ? { draft: await remote.revision(id) } : await remote.loadLatest()
      if (!alive.current) return
      setPast(h => [...h.slice(-29), previous]); setFuture([]); setDraft(result.draft); resetSelection()
      if (!id) { setSavedSnapshot(JSON.stringify(result.draft)); setCloud(c => ({ ...c, revision: result.revision })) }
      else setSavedSnapshot(null)
      setSaveError('')
    } catch (e) { if (alive.current) setSaveError(editFailure(e)) }
    finally { saving.current = false; if (alive.current) setCloud(c => ({ ...c, saving: false })) }
  }
  async function showHistory(before) {
    setHistoryBusy(true)
    try {
      const result = await remote.history(before)
      if (alive.current) setVersions(old => ({ ...result, revisions: before ? [...old.revisions, ...result.revisions] : result.revisions }))
    } catch { if (alive.current) setSaveError('Version history could not be loaded. Your current edit is intact.') }
    finally { if (alive.current) setHistoryBusy(false) }
  }
  function style(key, value) { commit({ ...draft, style: { ...draft.style, [key]: value } }) }
  function addText() {
    if (!duration || (draft.textLayers?.length || 0) >= 20) return
    const id = crypto.randomUUID(), begin = Math.min(Math.max(0, time), Math.max(0, duration - 0.1))
    const layer = { id, text: 'הטקסט שלך', start: begin, end: Math.min(duration, begin + 4), style: { ...DEFAULT_STYLE, x: 50, y: 35, alignment: 'center', opacity: 35 } }
    commit({ ...draft, textLayers: [...(draft.textLayers || []), layer] }); setSelectedText(id); setExpanded(true)
    if (video.current) { video.current.pause(); video.current.currentTime = begin }
    setTime(begin)
  }
  function editText(id, patch) {
    const layers = draft.textLayers || [], old = layers.find(layer => layer.id === id)
    if (!old) return
    const next = { ...old, ...patch, style: { ...old.style, ...patch.style } }
    try { validateCues([{ ...next, text: next.text.trim() ? next.text : 'Draft text' }], duration) } catch (e) { setError(e.message); return }
    commit({ ...draft, textLayers: layers.map(layer => layer.id === id ? next : layer) }); setError('')
  }
  function boundedPoint(x, y, node = captionBox.current) {
    if (!node) return clampSubtitlePoint(x, y)
    const stage = node.parentElement.getBoundingClientRect(), box = node.getBoundingClientRect()
    return clampSubtitlePoint(x, y, stage.width ? box.width / stage.width : 0, stage.height ? box.height / stage.height : 0)
  }
  function place(x, y) { commit({ ...draft, style: { ...draft.style, position: 'custom', ...boundedPoint(x, y) } }) }
  function dragStart(e) {
    if (draft.style.position !== 'custom' || e.button !== 0) return
    const stage = e.currentTarget.parentElement.getBoundingClientRect()
    if (!stage.width || !stage.height) return
    e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { id: e.pointerId, left: e.clientX, top: e.clientY, stage, x: draft.style.x ?? 50, y: draft.style.y ?? 80, point: null }
  }
  function dragMove(e) {
    const origin = drag.current
    if (!origin || origin.id !== e.pointerId) return
    origin.point = boundedPoint(origin.x + (e.clientX - origin.left) * 100 / origin.stage.width, origin.y + (e.clientY - origin.top) * 100 / origin.stage.height, e.currentTarget)
    setDragPoint(origin.point)
  }
  function dragEnd(e, cancelled = false) {
    const origin = drag.current
    if (!origin || origin.id !== e.pointerId) return
    if (!cancelled && origin.point && (origin.point.x !== origin.x || origin.point.y !== origin.y)) place(origin.point.x, origin.point.y)
    drag.current = null; setDragPoint(null)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  function syncAudio() {
    if (video.current) setAudio({ muted: video.current.muted, volume: video.current.volume })
  }
  function toggleAudio() {
    const v = video.current
    if (!v) return
    if (v.muted || v.volume === 0) { if (v.volume === 0) v.volume = 0.7; v.muted = false }
    else v.muted = true
    syncAudio()
  }
  function applyCommand(e) {
    e.preventDefault()
    const correction = subtitleStyleCommand(command)
    if (!correction) { setCommandStatus('Try: align subtitles left, center or right. אפשר גם בעברית.'); return }
    if (!draft.cues.length) { setCommandStatus('Add or import subtitles first.'); return }
    if (Object.entries(correction).some(([key, value]) => draft.style[key] !== value)) commit({ ...draft, style: { ...draft.style, ...correction } })
    setCommandStatus(`Subtitles: ${correction.position}, ${correction.alignment || draft.style.alignment || 'auto'} alignment. Use Undo above to reverse this change.`)
  }
  function choose(cue) { setSelected(cue.id); setCaption(cue.text); setStart(String(cue.start)); setEnd(String(cue.end)); if (video.current) video.current.currentTime = cue.start }
  function saveCue() {
    try {
      const cue = { id: selected || crypto.randomUUID(), text: caption, start: Number(start), end: Number(end) }
      const cues = selected ? draft.cues.map(c => c.id === selected ? cue : c) : [...draft.cues, cue]
      validateCues(cues, duration); commit({ ...draft, cues }); setSelected(cue.id); setError('')
    } catch (e) { setError(e.message) }
  }
  async function load(file) {
    const id = ++request.current
    if (!file) return
    try {
      if (file.size > 100000) throw new Error('Use an SRT file smaller than 100 KB.')
      const cues = parseSrt(await file.text(), duration)
      if (id !== request.current) return
      commit({ ...draft, cues }); setSelected(''); setCaption(''); setError('')
    } catch (e) { if (id === request.current) setError(e.message) }
  }
  function download() {
    const blobUrl = URL.createObjectURL(new Blob([toSrt(draft.cues, duration)], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a'); a.href = blobUrl; a.download = 'ben-subtitles.srt'; a.click()
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
  }
  if (!open) return null
  const current = activeSubtitle(draft.cues, time), s = draft.style
  const direction = subtitleDirection(current, subtitleDirection(draft.cues.map(c => c.text).join(' ')))
  const alignment = subtitleAlignment(s.alignment, direction)
  const activeText = draft.textLayers?.find(layer => layer.id === selectedText)
  return createPortal(<div className="ben-editor-backdrop">
    <section ref={dialog} className="ben-video-editor" role="dialog" aria-modal="true" aria-label="Video subtitle editor">
      <header className="ben-video-editor__header"><div><strong>Video editor</strong><span role="status" aria-label="Draft save status">{remote ? cloud.loading ? 'Opening saved edit…' : cloud.saving ? 'Saving / loading…' : cloud.pending ? 'Save unconfirmed · recovery available' : !cloud.ready ? 'Saved editing unavailable' : dirty ? 'Unsaved changes' : `Saved to BEN · version ${cloud.revision}` : !draftKey ? 'Session draft · local saving unavailable' : dirty ? 'Unsaved changes · this browser only' : 'Saved in this browser'}</span></div>
        <div className="ben-video-editor__actions" role="group" aria-label="Save and history">
          <button type="button" disabled={!past.length || cloud.saving || cloud.pending} onClick={undo} title="Undo · Ctrl/Cmd+Z">Undo</button>
          <button type="button" disabled={!future.length || cloud.saving || cloud.pending} onClick={redo} title="Redo · Ctrl/Cmd+Shift+Z">Redo</button>
          <button type="button" className="ben-video-editor__save" disabled={!draftKey || !duration || cloud.saving || (remote && !cloud.ready)} onClick={saveDraft} title="Save draft · Ctrl/Cmd+S">{cloud.pending ? 'Retry save' : 'Save'}</button>
          {onExport && <button type="button" disabled={dirty || !cloud.revision || cloud.saving || cloud.pending || exportBusy} onClick={exportMp4} title={dirty ? 'Save your changes before export' : 'Render this saved version to MP4'}>{exportBusy ? 'Exporting…' : 'Export MP4'}</button>}
          {remote && <button type="button" disabled={!cloud.revision || cloud.saving || cloud.pending || historyBusy} onClick={() => showHistory()}>Versions</button>}
        </div>
        <button ref={close} type="button" disabled={cloud.saving} onClick={requestClose} aria-label="Close video editor">✕</button></header>
      {onExport && (exportStatus || exportUrl) && <div className="ben-video-editor__export"><p role="status">{exportStatus}</p>{exportUrl && <a href={exportUrl} download="ben-edited-video.mp4">Download edited MP4</a>}</div>}
      {saveError && <p className="ben-video-editor__save-error" role="alert">{saveError}</p>}
      {remote && cloud.ready && remote.head && !cloud.pending && <button type="button" disabled={cloud.saving} onClick={() => loadVersion()}>Load latest · keep current changes in Undo</button>}
      {versions && <section className="ben-video-editor__versions" aria-label="Saved versions">
        <strong>Saved versions</strong><span> Open a version, then Save to restore it as a new version.</span>
        {versions.revisions.map(item => <button type="button" key={item.revision_id} disabled={cloud.saving || cloud.pending} onClick={() => loadVersion(item.revision_id)}>Version {item.revision_number} · {new Date(item.created_at).toLocaleString()}</button>)}
        {versions.next_before && <button type="button" disabled={historyBusy} onClick={() => showHistory(versions.next_before)}>Older versions</button>}
        <button type="button" onClick={() => setVersions(null)}>Close history</button>
      </section>}
      <div inert={remote && (!cloud.ready || cloud.saving || cloud.pending) ? true : undefined} className={`ben-video-editor__body ${expanded ? '' : 'ben-video-editor__body--collapsed'}`}>
        <div className="ben-video-editor__preview">
          <div className="ben-video-editor__stage" style={{ aspectRatio: ratio, width: `min(100%, calc(var(--editor-video-height, 54dvh) * ${ratio}))` }}>
            <video ref={video} src={url} controls playsInline onVolumeChange={syncAudio} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} aria-label="Subtitle video preview" onLoadedMetadata={e => {
              const v = e.currentTarget; setPlaying(!v.paused); setTime(v.currentTime); setDuration(v.duration); setRatio(v.videoWidth / v.videoHeight || 16 / 9); syncAudio()
              if (savedDuration.current && Math.abs(savedDuration.current - v.duration) > .1) setSaveError('This video differs from the saved draft. Check the text timing before saving.')
            }} onTimeUpdate={e => setTime(e.currentTarget.currentTime)} onSeeked={e => setTime(e.currentTarget.currentTime)} />
            {current && <div ref={captionBox} dir={direction} className={`ben-video-editor__caption ben-video-editor__caption--${s.position}`}
              role={s.position === 'custom' ? 'button' : undefined} tabIndex={s.position === 'custom' ? 0 : undefined} aria-label={s.position === 'custom' ? 'Move subtitle text' : undefined}
              onPointerDown={dragStart} onPointerMove={dragMove} onPointerUp={e => dragEnd(e)} onPointerCancel={e => dragEnd(e, true)} onLostPointerCapture={e => dragEnd(e, true)}
              onKeyDown={e => { if (s.position !== 'custom' || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return; e.preventDefault(); const step = e.shiftKey ? 5 : 1; place((s.x ?? 50) + (e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0), (s.y ?? 80) + (e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0)) }}
              style={{ ...textAppearance(s), textAlign: alignment, ...(s.position === 'custom' ? { left: `${dragPoint?.x ?? s.x ?? 50}%`, top: `${dragPoint?.y ?? s.y ?? 80}%` } : {}) }}>
              <span dir="auto" style={{ backgroundColor: textBackground(s) }}>{current}</span></div>}
            {(draft.textLayers || []).filter(layer => time >= layer.start && time < layer.end).map(layer => <FreeTextLayer key={layer.id} layer={layer} selected={selectedText === layer.id} onSelect={() => { setSelectedText(layer.id); setExpanded(true) }} onMove={point => editText(layer.id, { style: point })} />)}
          </div>
          <div className="ben-video-editor__playback">
          <button type="button" aria-label={playing ? 'Pause preview' : 'Play preview'} onClick={() => {
            if (playing) video.current?.pause()
            else video.current?.play().catch(() => setError('Video playback is unavailable.'))
          }}>{playing ? 'Pause' : 'Play'}</button>
          <button type="button" onClick={addText} disabled={!duration || (draft.textLayers?.length || 0) >= 20}>＋ Add Text</button>
          <button type="button" aria-label={audio.muted || audio.volume === 0 ? 'Turn sound on' : 'Mute sound'} onClick={toggleAudio}>
            <span aria-hidden="true">{audio.muted || audio.volume === 0 ? '🔇' : '🔊'}</span> {audio.muted || audio.volume === 0 ? 'Sound off' : 'Sound on'}
          </button>
          <label className="ben-video-editor__volume">Volume<input aria-label="Preview volume" type="range" min="0" max="100" value={Math.round(audio.volume * 100)} onChange={e => {
            const v = video.current; if (!v) return
            v.volume = Number(e.target.value) / 100; v.muted = v.volume === 0; syncAudio()
          }} /></label>
          </div>
          <form className="ben-video-editor__command" onSubmit={applyCommand}>
            <label>Quick correction<input aria-label="Subtitle correction" dir="auto" maxLength={160} value={command} onChange={e => { setCommand(e.target.value); setCommandStatus('') }} placeholder="מקם את הכתוביות במרכז" /></label>
            <button type="submit" disabled={!command.trim()}>Apply correction</button>
          </form>
          {commandStatus && <p className="ben-video-editor__feedback" role="status">{commandStatus}</p>}
          <p className="ben-video-editor__notice">{remote ? 'Save keeps your text, subtitles and styling privately in BEN. Open this video again to continue. ' : 'Save keeps subtitles, text layers, styling and positions in this browser for this video. It does not sync to another device. '}{onExport ? 'Export MP4 renders the saved version with its text and styling. ' : ''}SRT includes subtitles only. Sound controls affect preview playback.</p>
          <button type="button" aria-expanded={expanded} aria-controls="subtitle-design-panel" onClick={() => setExpanded(v => !v)}>{expanded ? 'Hide controls' : 'Show controls'}</button>
        </div>
        {expanded && <aside id="subtitle-design-panel" className="ben-video-editor__panel" aria-label="Subtitle controls">
          {!!draft.textLayers?.length && <label>Text layers<select aria-label="Text layer" value={activeText?.id || ''} onChange={e => { setSelectedText(e.target.value); const layer = draft.textLayers.find(t => t.id === e.target.value); if (layer && video.current) { video.current.currentTime = layer.start; setTime(layer.start) } }}><option value="">Choose text</option>{draft.textLayers.map(layer => <option key={layer.id} value={layer.id}>{layer.text.slice(0,40)}</option>)}</select></label>}
          {activeText && <section className="ben-video-editor__text-properties" aria-label="Free text controls">
            <h3>Text layer · independent of subtitles</h3>
            <label>Overlay text<textarea aria-label="Overlay text" dir="auto" maxLength={1000} value={activeText.text} onChange={e => editText(activeText.id,{text:e.target.value})} /></label>
            <div className="ben-video-editor__times">
              <label>Text starts (s)<input type="number" min="0" max={duration} step="0.1" value={activeText.start} onChange={e => editText(activeText.id,{start:Number(e.target.value)})} /></label>
              <label>Text ends (s)<input type="number" min="0" max={duration} step="0.1" value={activeText.end} onChange={e => editText(activeText.id,{end:Number(e.target.value)})} /></label>
            </div>
            <TextAppearanceControls prefix="Text layer" style={activeText.style} onChange={(key,value)=>editText(activeText.id,{style:{[key]:value}})} />
            <label>Layer alignment<select aria-label="Layer alignment" value={activeText.style.alignment || 'auto'} onChange={e=>editText(activeText.id,{style:{alignment:e.target.value}})}><option value="auto">Automatic · by language</option><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></label>
            <p>Drag this text on the video, or use arrow keys. Its style and timing are separate from subtitles.</p>
            <button type="button" onClick={()=>{commit({...draft,textLayers:draft.textLayers.filter(layer=>layer.id!==activeText.id)});setSelectedText('')}}>Remove text layer</button>
          </section>}
          <label>Import subtitles (.srt)<input disabled={!duration} type="file" accept=".srt" onChange={e => { void load(e.target.files?.[0]); e.target.value = '' }} /></label>
          <p>No automatic transcription or translation in this editor yet. Add text below or import SRT.</p>
          <label>Subtitle<select value={selected} onChange={e => { const cue = draft.cues.find(c => c.id === e.target.value); if (cue) choose(cue); else { setSelected(''); setCaption('') } }}>
            <option value="">New subtitle</option>{draft.cues.map(c => <option key={c.id} value={c.id}>{c.start.toFixed(1)}s · {c.text.slice(0,45)}</option>)}</select></label>
          <label>Text<textarea dir="auto" rows={2} maxLength={1000} value={caption} onChange={e => setCaption(e.target.value)} /></label>
          <div className="ben-video-editor__times"><label>Start (s)<input type="number" min="0" step="0.01" value={start} onChange={e => setStart(e.target.value)} /></label>
            <label>End (s)<input type="number" min="0" step="0.01" value={end} onChange={e => setEnd(e.target.value)} /></label></div>
          <button disabled={!duration || !caption.trim()} onClick={saveCue}>{selected ? 'Apply text' : 'Add subtitle'}</button>
          <h3>Design · all subtitles</h3>
          <TextAppearanceControls prefix="Subtitles" style={s} onChange={style} />
          <label>Vertical placement<select aria-label="Vertical placement" value={s.position} onChange={e => style('position',e.target.value)}><option value="bottom">Bottom (default)</option><option value="center">Middle of screen</option><option value="top">Top</option><option value="custom">Free placement · drag text</option></select></label>
          {s.position === 'custom' && <div className="ben-video-editor__free-position">
            <p>Drag the text inside the video. Arrow keys move it precisely; Shift moves faster. Applies to all subtitles.</p>
            <label>Horizontal position · {Math.round(dragPoint?.x ?? s.x ?? 50)}%<input aria-label="Horizontal text position" type="range" min="0" max="100" value={dragPoint?.x ?? s.x ?? 50} onChange={e => place(Number(e.target.value), s.y ?? 80)} /></label>
            <label>Vertical position · {Math.round(dragPoint?.y ?? s.y ?? 80)}%<input aria-label="Vertical text position" type="range" min="0" max="100" value={dragPoint?.y ?? s.y ?? 80} onChange={e => place(s.x ?? 50, Number(e.target.value))} /></label>
          </div>}
          <label>Text alignment<select aria-label="Text alignment" value={s.alignment || 'auto'} onChange={e => style('alignment',e.target.value)}><option value="auto">Automatic · by language</option><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></label>
          <p>Default: bottom, Hebrew right / English left. Center alignment keeps subtitles at the bottom; middle of screen is a separate placement.</p>
          <button disabled={!draft.cues.length} onClick={download}>Download subtitles (.srt)</button>
          {error && <p role="alert">{error}</p>}
        </aside>}
      </div>
    </section>
  </div>, document.body)
}
