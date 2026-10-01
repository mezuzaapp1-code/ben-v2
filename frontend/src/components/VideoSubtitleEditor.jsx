import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { activeSubtitle, DEFAULT_STYLE, FONTS, parseSrt, toSrt, validateCues } from '../lib/subtitleDraft.js'
import './VideoSubtitleEditor.css'

export default function VideoSubtitleEditor({ url, open, onClose }) {
  const [draft, setDraft] = useState({ cues: [], style: DEFAULT_STYLE })
  const [past, setPast] = useState([]), [future, setFuture] = useState([])
  const [expanded, setExpanded] = useState(true), [selected, setSelected] = useState('')
  const [time, setTime] = useState(0), [duration, setDuration] = useState(0)
  const [error, setError] = useState(''), [caption, setCaption] = useState('')
  const [start, setStart] = useState('0'), [end, setEnd] = useState('2')
  const [playing, setPlaying] = useState(false)
  const [ratio, setRatio] = useState(16 / 9)
  const dialog = useRef(null), video = useRef(null), close = useRef(null), request = useRef(0)
  const closeAction = useRef(onClose); closeAction.current = onClose
  useEffect(() => {
    if (!open) return undefined
    const previous = document.activeElement, overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'; close.current?.focus()
    function keyboard(e) {
      if (e.key === 'Escape') { e.preventDefault(); closeAction.current() }
      if (e.key !== 'Tab') return
      const items = [...dialog.current.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select,textarea,video[controls]')].filter(el => el.getClientRects().length)
      const first = items[0], last = items.at(-1)
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keyboard)
    return () => { request.current++; document.removeEventListener('keydown', keyboard); document.body.style.overflow = overflow; previous?.focus?.() }
  }, [open])
  function commit(next) { setPast(h => [...h.slice(-29), draft]); setFuture([]); setDraft(next) }
  function style(key, value) { commit({ ...draft, style: { ...draft.style, [key]: value } }) }
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
  return createPortal(<div className="ben-editor-backdrop">
    <section ref={dialog} className="ben-video-editor" role="dialog" aria-modal="true" aria-label="Video subtitle editor">
      <header className="ben-video-editor__header"><div><strong>Video subtitles</strong><span>Preview and timed text</span></div>
        <button ref={close} type="button" onClick={onClose} aria-label="Close video editor">✕</button></header>
      <div className={`ben-video-editor__body ${expanded ? '' : 'ben-video-editor__body--collapsed'}`}>
        <div className="ben-video-editor__preview">
          <div className="ben-video-editor__stage" style={{ aspectRatio: ratio, width: `min(100%, calc(var(--editor-video-height, 54dvh) * ${ratio}))` }}>
            <video ref={video} src={url} controls playsInline onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} aria-label="Subtitle video preview" onLoadedMetadata={e => {
              const v = e.currentTarget; setTime(v.currentTime); setDuration(v.duration); setRatio(v.videoWidth / v.videoHeight || 16 / 9)
            }} onTimeUpdate={e => setTime(e.currentTarget.currentTime)} onSeeked={e => setTime(e.currentTarget.currentTime)} />
            {current && <div className={`ben-video-editor__caption ben-video-editor__caption--${s.position}`}
              style={{ fontFamily: `${s.font}, sans-serif`, fontSize: `${s.size}cqw`, color: s.color }}>
              <span dir="auto" style={{ backgroundColor: s.background + Math.round(s.opacity * 255 / 100).toString(16).padStart(2,'0') }}>{current}</span></div>}
          </div>
          <button type="button" aria-label={playing ? 'Pause preview' : 'Play preview'} onClick={() => {
            if (playing) video.current?.pause()
            else video.current?.play().catch(() => setError('Video playback is unavailable.'))
          }}>{playing ? 'Pause' : 'Play'}</button>
          <p className="ben-video-editor__notice">Your original video is unchanged. This draft stays while this conversation is open. Download SRT to keep the text; fonts and colors are preview-only.</p>
          <button type="button" aria-expanded={expanded} aria-controls="subtitle-design-panel" onClick={() => setExpanded(v => !v)}>{expanded ? 'Hide controls' : 'Show controls'}</button>
        </div>
        {expanded && <aside id="subtitle-design-panel" className="ben-video-editor__panel" aria-label="Subtitle controls">
          <div className="ben-video-editor__history"><button disabled={!past.length} onClick={() => { setFuture(h => [draft,...h]); setDraft(past.at(-1)); setPast(h => h.slice(0,-1)); setSelected(''); setCaption('') }}>Undo</button>
            <button disabled={!future.length} onClick={() => { setPast(h => [...h,draft]); setDraft(future[0]); setFuture(h => h.slice(1)); setSelected(''); setCaption('') }}>Redo</button></div>
          <label>Import subtitles (.srt)<input disabled={!duration} type="file" accept=".srt" onChange={e => { void load(e.target.files?.[0]); e.target.value = '' }} /></label>
          <p>No automatic transcription or translation in this editor yet. Add text below or import SRT.</p>
          <label>Subtitle<select value={selected} onChange={e => { const cue = draft.cues.find(c => c.id === e.target.value); if (cue) choose(cue); else { setSelected(''); setCaption('') } }}>
            <option value="">New subtitle</option>{draft.cues.map(c => <option key={c.id} value={c.id}>{c.start.toFixed(1)}s · {c.text.slice(0,45)}</option>)}</select></label>
          <label>Text<textarea dir="auto" rows={2} maxLength={1000} value={caption} onChange={e => setCaption(e.target.value)} /></label>
          <div className="ben-video-editor__times"><label>Start (s)<input type="number" min="0" step="0.01" value={start} onChange={e => setStart(e.target.value)} /></label>
            <label>End (s)<input type="number" min="0" step="0.01" value={end} onChange={e => setEnd(e.target.value)} /></label></div>
          <button disabled={!duration || !caption.trim()} onClick={saveCue}>{selected ? 'Apply text' : 'Add subtitle'}</button>
          <h3>Design · all subtitles</h3>
          <label>Font<select value={s.font} onChange={e => style('font',e.target.value)}>{FONTS.map(f => <option key={f}>{f}</option>)}</select></label>
          <label>Size<input type="range" min="2" max="9" step="0.5" value={s.size} onChange={e => style('size',Number(e.target.value))} /></label>
          <div className="ben-video-editor__times"><label>Text color<input type="color" value={s.color} onChange={e => style('color',e.target.value)} /></label>
            <label>Background<input type="color" value={s.background} onChange={e => style('background',e.target.value)} /></label></div>
          <label>Background opacity<input type="range" min="0" max="100" value={s.opacity} onChange={e => style('opacity',Number(e.target.value))} /></label>
          <label>Position<select value={s.position} onChange={e => style('position',e.target.value)}>{['top','center','bottom'].map(p => <option key={p}>{p}</option>)}</select></label>
          <button disabled={!draft.cues.length} onClick={download}>Download subtitles (.srt)</button>
          {error && <p role="alert">{error}</p>}
        </aside>}
      </div>
    </section>
  </div>, document.body)
}
