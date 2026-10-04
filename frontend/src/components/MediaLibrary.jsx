import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { mediaRequest } from '../api/media.js'
import { useUiLocale } from '../hooks/useUiLocale.js'
import MediaAsset from './MediaAsset.jsx'
import './MediaLibrary.css'

export function MediaLibraryNav({ onOpen, disabled }) {
  const he = useUiLocale() === 'he'
  return <nav className="ben-media-nav" aria-label={he ? 'יצירה ומדיה' : 'Create and edit'}>
    <button type="button" disabled={disabled} aria-haspopup="dialog" onClick={() => onOpen('editor')}>
      <span aria-hidden="true">✎</span> {he ? 'עורך מדיה' : 'Media editor'}
    </button>
    <button type="button" disabled={disabled} aria-haspopup="dialog" onClick={() => onOpen('saved')}>
      <span aria-hidden="true">▣</span> {he ? 'העבודות שלי' : 'My saved work'}
    </button>
  </nav>
}

// Mounted per account and per opening. No private account data is kept on navigation.
export default function MediaLibrary({ initialView = 'saved', scope, conversationId, buildHeaders, onClose }) {
  const he = useUiLocale() === 'he'
  const t = (en, hebrew) => he ? hebrew : en
  const [view, setView] = useState(initialView)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(false), [retry, setRetry] = useState(0)
  const [selected, setSelected] = useState(null), [editing, setEditing] = useState(false)
  const panel = useRef(null), close = useRef(null)
  const editingRef = useRef(false), closeRef = useRef(onClose)
  useEffect(() => { editingRef.current = editing; closeRef.current = onClose })
  useEffect(() => {
    const previous = document.activeElement, overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    close.current?.focus()
    function keyboard(event) {
      // The editor has its own focus trap and unsaved-change / in-flight-save guard.
      if (editingRef.current) return
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); return }
      if (event.key !== 'Tab') return
      const items = [...panel.current.querySelectorAll('button:not(:disabled),a[href],video[controls]')]
      const first = items[0], last = items.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keyboard)
    return () => {
      document.removeEventListener('keydown', keyboard)
      document.body.style.overflow = overflow
      if (previous?.isConnected) previous.focus()
    }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const headers = await buildHeaders()
        const caps = await mediaRequest('/capabilities', headers, { signal: controller.signal })
        if (controller.signal.aborted) return
        if (caps.edit_documents !== true) { setResult({ unavailable: true }); return }
        const response = view === 'saved'
          ? await mediaRequest('/edit-documents', headers, { signal: controller.signal })
          : conversationId ? await mediaRequest(`/executions?conversation_id=${encodeURIComponent(conversationId)}`, headers, { signal: controller.signal }) : { executions: [] }
        if (!controller.signal.aborted) setResult({ items: view === 'saved' ? response.documents : response.executions.filter(row => row.status === 'succeeded' && row.resource_id && row.mime_type === 'video/mp4') })
      } catch { if (!controller.signal.aborted) setError(true) }
    }
    void load()
    return () => controller.abort()
  }, [scope, buildHeaders, conversationId, view, retry])
  function changeView(next) {
    if (next === view) return
    setSelected(null); setResult(null); setError(false); setView(next)
  }
  return createPortal(<div className="ben-media-library-backdrop">
    <section ref={panel} className="ben-media-library" role="dialog" aria-modal="true" aria-label={t('Media studio', 'סטודיו מדיה')} dir={he ? 'rtl' : 'ltr'} inert={editing ? true : undefined}>
      <header><div><small>BEN STUDIO</small><h2>{t('Your work. Ready to edit.', 'העבודות שלך. מוכנות לעריכה.')}</h2></div>
        <button ref={close} type="button" onClick={onClose} aria-label={t('Close media studio', 'סגירת סטודיו המדיה')}>✕</button></header>
      <div className="ben-media-library__tabs" role="group" aria-label={t('Work selection', 'בחירת עבודות')}>
        <button type="button" aria-pressed={view === 'saved'} onClick={() => changeView('saved')}>{t('My saved work', 'העבודות שלי')}</button>
        <button type="button" aria-pressed={view === 'editor'} onClick={() => changeView('editor')}>{t('Videos in this chat', 'סרטונים בשיחה זו')}</button>
      </div>
      <div className="ben-media-library__body">
        <p>{view === 'saved' ? t('Open a saved video edit to continue. Images and other creations are still in their original chats.', 'פתיחת עריכת וידאו שמורה להמשך עבודה. תמונות ויצירות אחרות נמצאות עדיין בשיחות שבהן נוצרו.') : t('Choose a completed video from this chat, or open a saved edit from My saved work.', 'בחרו סרטון מוכן מהשיחה הזו, או פתחו עריכה שמורה מתוך העבודות שלי.')}</p>
        {error ? <div role="alert"><p>{t('Could not load your work. Your saved edits have not changed.', 'לא ניתן לטעון את העבודות. העריכות השמורות לא השתנו.')}</p><button type="button" onClick={() => { setError(false); setResult(null); setRetry(value => value + 1) }}>{t('Try again', 'ניסיון נוסף')}</button></div>
          : !result ? <p role="status">{t('Loading your work…', 'טוען את העבודות…')}</p>
            : result.unavailable ? <p role="status">{t('Media editing is not enabled for this account yet.', 'עריכת מדיה עדיין אינה פעילה בחשבון הזה.')}</p>
              : <>
                {result.items.length === 0 && <div className="ben-media-library__empty"><h3>{t('A place for your next creation', 'מקום ליצירה הבאה שלך')}</h3><p>{view === 'saved' ? t('No saved video edits yet. Open a video, edit it and press Save to find it here.', 'אין עדיין עריכות וידאו שמורות. פתחו סרטון, ערכו ושמרו באמצעות Save כדי למצוא אותו כאן.') : t('No completed videos in this chat. Return to a chat containing a video, then open Media editor again.', 'אין סרטונים מוכנים בשיחה הזו. עברו לשיחה עם סרטון ופתחו שוב את עורך המדיה.')}</p></div>}
                <div className="ben-media-library__grid">{result.items.map((item, index) => <button className="ben-media-library__card" type="button" key={item.document_id || item.execution_id} onClick={() => { setSelected(item); setEditing(true) }}>
                  <span className="ben-media-library__symbol" aria-hidden="true">▷</span>
                  <strong>{t('Video', 'סרטון')} {result.items.length - index}</strong>
                  <span>{item.head_number ? `${t('Version', 'גרסה')} ${item.head_number} · ` : ''}{new Date(item.updated_at || item.created_at).toLocaleString(he ? 'he-IL' : undefined)}</span>
                  <span>{t('Open in editor →', 'פתיחה בעורך ←')}</span>
                </button>)}</div>
              </>}
      </div>
    </section>
    {/* Outside the inert library: the editor owns close/save confirmation. */}
    {selected && <MediaAsset key={`${scope}:${selected.document_id || selected.resource_id}`} resourceId={selected.resource_id} documentId={selected.document_id} mimeType="video/mp4" scope={scope} savedEditing editorOnly buildHeaders={buildHeaders} editing={editing}
      onEdit={() => setEditing(true)} onClose={() => { setEditing(false); setSelected(null); setResult(null); setRetry(value => value + 1); close.current?.focus() }} />}
  </div>, document.body)
}
