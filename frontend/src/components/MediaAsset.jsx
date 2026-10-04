import { useEffect, useRef, useState } from 'react'
import VideoSubtitleEditor from './VideoSubtitleEditor.jsx'
import SavedVideoEditor from './SavedVideoEditor.jsx'
import { mediaRequest } from '../api/media.js'

export default function MediaAsset({ resourceId, buildHeaders, mimeType, editing, onEdit, onClose, scope, savedEditing, documentId, editorOnly = false }) {
  const player = useRef(null)
  useEffect(() => { if (editing) player.current?.pause() }, [editing])
  const [loaded, setLoaded] = useState(null)
  const current = loaded?.resourceId === resourceId && loaded?.headers === buildHeaders ? loaded : null
  const url = current?.url, error = current?.error
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl
    async function load() {
      try {
        const bytes = await mediaRequest(`/resources/${resourceId}/content`, await buildHeaders(),
          { binary: true, signal: controller.signal })
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(bytes)
        setLoaded({ resourceId, headers: buildHeaders, url: objectUrl })
      } catch {
        if (!controller.signal.aborted) setLoaded({ resourceId, headers: buildHeaders, error: true })
      }
    }
    void load()
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [resourceId, buildHeaders])
  if (editorOnly) return url
    ? <SavedVideoEditor resourceId={resourceId} documentId={documentId} scope={scope} buildHeaders={buildHeaders} url={url} open={editing} onClose={onClose} />
    : <div className="ben-media-opening" role="dialog" aria-modal="true" aria-label="Opening video editor" onKeyDown={event => {
      if (event.key === 'Escape') { event.stopPropagation(); onClose() }
      if (event.key === 'Tab') event.preventDefault()
    }}><p role={error ? 'alert' : 'status'}>{error ? 'Video unavailable. Close and try again.' : 'Opening video…'}</p><button type="button" autoFocus onClick={onClose}>Back to my work</button></div>
  if (url && mimeType === 'video/mp4') return <div>
    <video ref={player} src={url} controls preload="metadata" aria-label="BEN video" style={{ objectFit: 'contain', maxWidth: '100%', maxHeight: 360 }} />
    <button type="button" onClick={onEdit}>Edit video</button>
    <a href={url} download="ben-video.mp4">Download video</a>
    {savedEditing ? <SavedVideoEditor key={`${scope}:${resourceId}:${documentId || ''}`} resourceId={resourceId} documentId={documentId} scope={scope} buildHeaders={buildHeaders} url={url} open={editing} onClose={onClose} />
      : <VideoSubtitleEditor draftKey={`${scope}:${resourceId}`} url={url} open={editing} onClose={onClose} />}
  </div>
  return url ? <a href={url} download="ben-image.png"><img src={url} alt="BEN generated image" style={{ maxWidth: '100%', maxHeight: 360 }} /></a>
    : <p>{error ? 'Media unavailable. Reopen to retry.' : 'Loading imageâ€¦'}</p>
}

