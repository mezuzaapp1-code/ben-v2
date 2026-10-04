import { useEffect, useMemo } from 'react'
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
  return <VideoSubtitleEditor {...props} remote={session} draftKey={`${scope}:${resourceId}`} />
}
