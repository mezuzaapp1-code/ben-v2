import { useEffect, useMemo, useState } from 'react'
import { sanitizeResponseEvidence } from '../lib/fileStatus.js'
import {
  openWorkspaceFilePreview,
  revokePreviewUrl,
} from '../lib/workspaceFilePreview.js'
import './SourcesPanel.css'

function evidenceForSource(evidence, sourceId) {
  return (evidence || []).filter((item) => item.source_id === sourceId)
}

export function SourcesPanel({
  evidence,
  workspaceId,
  buildHeaders,
  onClose,
}) {
  const payload = useMemo(() => sanitizeResponseEvidence(evidence), [evidence])
  const [preview, setPreview] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)

  const allowedIds = useMemo(
    () => new Set((payload?.sources || []).map((s) => s.source_id)),
    [payload]
  )

  useEffect(() => {
    return () => {
      revokePreviewUrl(preview?.blobUrl)
    }
  }, [preview?.blobUrl])

  useEffect(() => {
    setError(null)
    setBusyId(null)
    setPreview((current) => {
      revokePreviewUrl(current?.blobUrl)
      return null
    })
  }, [payload])

  const openFile = async (fileId, page) => {
    if (!workspaceId || !buildHeaders) {
      setError('File preview is unavailable until the workspace is ready.')
      return
    }
    setBusyId(fileId)
    setError(null)
    try {
      const headers = await buildHeaders()
      const next = await openWorkspaceFilePreview({
        workspaceId,
        fileId,
        page,
        headers,
        allowedSourceIds: allowedIds,
      })
      setPreview((current) => {
        revokePreviewUrl(current?.blobUrl)
        return next
      })
    } catch (err) {
      setError(err?.message || 'Preview failed')
    } finally {
      setBusyId(null)
    }
  }

  if (!payload) return null
  const n = payload.sources.length

  return (
    <aside className="sources-panel" aria-label="Sources">
      <header className="sources-panel__header">
        <h2 className="sources-panel__title">Sources ({n})</h2>
        <button type="button" className="sources-panel__close" onClick={onClose} aria-label="Close sources">
          Close
        </button>
      </header>
      <div className="sources-panel__body">
        {payload.sources.map((source) => {
          const rows = evidenceForSource(payload.evidence, source.source_id)
          return (
            <section key={source.source_id} className="sources-panel__source">
              <div className="sources-panel__source-head">
                <div className="sources-panel__name" dir="auto">{source.display_name}</div>
                <div className="sources-panel__count">
                  {rows.length === 1 ? '1 evidence item' : `${rows.length} evidence items`}
                </div>
              </div>
              {rows.map((item) => (
                <div key={item.evidence_id}>
                  {item.page != null ? (
                    <span className="sources-panel__page">Page {item.page}</span>
                  ) : null}
                  <pre className="sources-panel__excerpt" dir="auto">{item.excerpt}</pre>
                  <div className="sources-panel__actions">
                    <button
                      type="button"
                      className="sources-panel__action"
                      disabled={busyId === source.source_id}
                      onClick={() => void openFile(source.source_id)}
                    >
                      Open source
                    </button>
                    {item.page != null ? (
                      <button
                        type="button"
                        className="sources-panel__action"
                        disabled={busyId === source.source_id}
                        onClick={() => void openFile(source.source_id, item.page)}
                      >
                        Open page {item.page}
                      </button>
                    ) : null}
                  </div>
                </div>
              ))}
              {!rows.length ? (
                <div className="sources-panel__actions">
                  <button
                    type="button"
                    className="sources-panel__action"
                    disabled={busyId === source.source_id}
                    onClick={() => void openFile(source.source_id)}
                  >
                    Open source
                  </button>
                </div>
              ) : null}
            </section>
          )
        })}
        {error ? <p className="sources-panel__error">{error}</p> : null}
      </div>
      {preview ? (
        <div className="sources-panel__preview">
          <div className="sources-panel__preview-title">{preview.displayName}</div>
          {preview.kind === 'pdf' && preview.src ? (
            <iframe title={preview.displayName} className="sources-panel__frame" src={preview.src} />
          ) : null}
          {preview.kind === 'image' && preview.src ? (
            <img alt={preview.displayName} className="sources-panel__img" src={preview.src} />
          ) : null}
          {preview.kind === 'text' ? (
            <pre className="sources-panel__text">{preview.text || '(empty)'}</pre>
          ) : null}
        </div>
      ) : null}
    </aside>
  )
}
