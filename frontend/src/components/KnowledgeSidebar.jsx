import { useRef, useState } from 'react'
import { FileLifecycleStatus } from './FileLifecycleStatus.jsx'
import { useWorkspaceFileInventory, workspaceFileInventory } from '../hooks/useWorkspaceFileInventory.jsx'
import { deriveFileStage, formatByteSize, processingPercent, visualFileStage } from '../lib/fileStatus.js'
import './KnowledgeSidebar.css'

export function KnowledgeSidebar({
  projectSlug,
  workspaceId = null,
  buildHeaders,
  disabled = false,
  attentionFocusRequest: _attentionFocusRequest = null,
  onOpenFileLibrary = null,
}) {
  const inputRef = useRef(null)
  const inventory = useWorkspaceFileInventory()
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)
  const files = workspaceId ? inventory.rows : []
  const loading = workspaceId ? inventory.loading : false
  const activeUpload = (inventory.uploads || []).find((item) => item.phase === 'uploading')
  const progress = processingPercent(null, activeUpload)

  const handlePickFile = () => {
    if (disabled || uploading) return
    if (!workspaceId) {
      setError('Select an active workspace/project before uploading.')
      onOpenFileLibrary?.()
      return
    }
    inputRef.current?.click()
  }

  const handleFileChange = async (event) => {
    const picked = event.target.files?.[0] || null
    event.target.value = ''
    if (!picked || !buildHeaders) return
    if (!workspaceId) {
      setError('Select an active workspace/project before uploading.')
      return
    }

    setUploading(true)
    setError(null)

    try {
      await workspaceFileInventory.uploadFile(picked)
    } catch (e) {
      setError(e?.message || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  if (!projectSlug && !workspaceId) {
    return null
  }

  return (
    <section className="knowledge-sidebar" aria-label="Project knowledge repository">
      <div className="knowledge-sidebar__upload">
        <p className="knowledge-sidebar__upload-label">📁 Workspace Files</p>
        <p className="knowledge-sidebar__hint">
          Upload into the Workspace File Library (PDF, Office, text, images — max 50 MB).
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.doc,.txt,.md,.markdown,.csv,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp,.json,application/pdf,text/plain,text/markdown,text/csv,image/*"
          className="knowledge-sidebar__file-input"
          disabled={disabled || uploading || !workspaceId}
          onChange={(e) => void handleFileChange(e)}
        />
        <button
          type="button"
          className="knowledge-sidebar__upload-btn"
          disabled={disabled || uploading}
          onClick={handlePickFile}
        >
          {uploading ? `Uploading… ${progress}%` : '+ Upload file'}
        </button>
        {onOpenFileLibrary ? (
          <button
            type="button"
            className="knowledge-sidebar__upload-btn"
            style={{ marginTop: '0.35rem' }}
            disabled={disabled}
            onClick={() => onOpenFileLibrary()}
          >
            Open File Library
          </button>
        ) : null}
        {(uploading || progress != null) && (
          <div className="knowledge-sidebar__progress" aria-label="Upload progress">
            <div
              className="knowledge-sidebar__progress-bar"
              style={{ width: `${progress ?? 0}%` }}
            />
          </div>
        )}
      </div>

      <div className="knowledge-sidebar__focus" aria-live="polite">
        <p className="knowledge-sidebar__focus-title">🎯 Active Context Focus</p>
        <p className="knowledge-sidebar__hint">
          Legacy project attention is unavailable. Existing data was not deleted.
          Workspace Files retrieval is unchanged.
        </p>
      </div>

      {(error || inventory.error) ? (
        <p className="knowledge-sidebar__error">{error || inventory.error}</p>
      ) : null}

      <div className="knowledge-sidebar__list">
        <div className="knowledge-sidebar__list-header">
          <span>Repository files</span>
          <button
            type="button"
            className="knowledge-sidebar__refresh"
            disabled={disabled || loading || uploading}
            onClick={() => void workspaceFileInventory.refresh()}
          >
            ↻
          </button>
        </div>
        {loading ? (
          <p className="knowledge-sidebar__hint">Loading…</p>
        ) : files.length === 0 ? (
          <p className="knowledge-sidebar__hint">No files uploaded yet.</p>
        ) : (
          <ul className="knowledge-sidebar__files">
            {files.map((file, index) => (
              <li key={file.id || `${file.display_name || file.name}-${index}`} className="knowledge-sidebar__file-row">
                <div className="knowledge-sidebar__file-main">
                  <span
                    className="knowledge-sidebar__file-name"
                    title={file.display_name || file.original_filename || file.name}
                  >
                    {file.display_name || file.original_filename || file.name}
                  </span>
                  <FileLifecycleStatus
                    className={`knowledge-sidebar__file-status knowledge-sidebar__file-status--${visualFileStage(deriveFileStage(file, { upload: file.upload }))}`}
                    file={file}
                    upload={file.upload}
                  />
                </div>
                <span className="knowledge-sidebar__file-meta">{formatByteSize(file.byte_size ?? file.size)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
