import { useEffect, useMemo } from 'react'
import { BEN_API_BASE } from '../config.js'
import { rasterizeTextTrack } from '../lib/renderTextTrack.js'
import { mediaRequest } from '../api/media.js'
import { EditSession } from '../lib/editSession.js'
import VideoSubtitleEditor from './VideoSubtitleEditor.jsx'

export default function SavedVideoEditor({ resourceId, scope, buildHeaders, documentId, ...props }) {
  const session = useMemo(() => new EditSession({ resourceId, scope, documentId,
    storage: { getItem: key => window.localStorage.getItem(key),
      setItem: (key, value) => window.localStorage.setItem(key, value),
      removeItem: key => window.localStorage.removeItem(key) },
  }), [resourceId, scope, documentId])
  useEffect(() => {
    session.setRequest(async (path, options = {}) => mediaRequest(path,
      { ...await buildHeaders(), ...(options.key ? { 'Idempotency-Key': options.key } : {}) },
      { body: options.body, signal: AbortSignal.timeout(30000) }))
  }, [session, buildHeaders])
  async function exportSaved(width, height, signal, onProgress) {
    if (!session.head || session.pending || session.busy) throw Error('Save this edit before exporting.')
    const revision = session.head
    const payload = await rasterizeTextTrack(revision.document, width, height, signal, onProgress)
    onProgress('Rendering MP4…')
    const headers = { ...await buildHeaders(), 'Content-Type': 'application/json' }
    signal.throwIfAborted()
    const response = await fetch(`${BEN_API_BASE}/api/media/edit-documents/${session.documentId}/revisions/${revision.revision_id}/export`, {
      method: 'POST', headers, body: JSON.stringify(payload), signal,
    })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw Error(body.detail?.message || 'Export failed. Your saved edit is unchanged; please retry.')
    }
    const blob = await response.blob()
    if (!blob.size || blob.type !== 'video/mp4') throw Error('Invalid export response. Please retry.')
    signal.throwIfAborted()
    return blob
  }
  return <VideoSubtitleEditor {...props} onExport={exportSaved} remote={session} draftKey={`${scope}:${resourceId}`} />
}
