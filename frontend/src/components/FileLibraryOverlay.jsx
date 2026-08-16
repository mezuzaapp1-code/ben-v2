import PropTypes from 'prop-types'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  deleteWorkspaceFile,
  fetchWorkspaceFileBlob,
  getWorkspaceFile,
  listWorkspaceFiles,
  retryWorkspaceFile,
  uploadWorkspaceFile,
} from '../api/workspaceFiles.js'
import {
  createBoundedStatusPoller,
  fileStatusLabel,
  isNonTerminalFileStatus,
} from '../lib/fileStatus.js'
import './FileLibraryOverlay.css'

const ACCEPT =
  '.pdf,.docx,.doc,.txt,.md,.markdown,.csv,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp,.json,application/pdf,text/plain,text/markdown,text/csv,image/*'

function formatBytes(n) {
  const v = Number(n) || 0
  if (v < 1024) return `${v} B`
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`
  return `${(v / (1024 * 1024)).toFixed(1)} MB`
}

function formatWhen(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function fileExt(name) {
  const parts = String(name || '').split('.')
  return parts.length > 1 ? parts.pop().toUpperCase() : 'FILE'
}

export function FileLibraryNavTrigger({ onOpen, active = false, disabled = false }) {
  return (
    <button
      type="button"
      className={`files-nav-trigger${active ? ' files-nav-trigger--active' : ''}`}
      onClick={onOpen}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-current={active ? 'page' : undefined}
    >
      <span>Files</span>
      <span className="files-nav-trigger__chevron" aria-hidden="true">
        ▸
      </span>
    </button>
  )
}

FileLibraryNavTrigger.propTypes = {
  onOpen: PropTypes.func.isRequired,
  active: PropTypes.bool,
  disabled: PropTypes.bool,
}

FileLibraryNavTrigger.defaultProps = {
  active: false,
  disabled: false,
}

/**
 * Workspace File Library — upload, list, search, preview, download.
 */
export function FileLibraryOverlay({
  open,
  onClose,
  workspaceId,
  workspaceName,
  buildHeaders,
  disabled = false,
}) {
  const [view, setView] = useState('all') // all | recent | processing | failed
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [q, setQ] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState(null)
  const [dragActive, setDragActive] = useState(false)
  const [preview, setPreview] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const fileInputRef = useRef(null)
  const itemsRef = useRef(items)
  itemsRef.current = items

  const statusFilter = useMemo(() => {
    if (view === 'processing') return 'processing'
    if (view === 'failed') return 'failed'
    return undefined
  }, [view])

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!open || !workspaceId || !buildHeaders) return
    if (!silent) {
      setLoading(true)
      setError(null)
    }
    try {
      const headers = await buildHeaders()
      const data = await listWorkspaceFiles(workspaceId, headers, {
        status: statusFilter,
        q: q || undefined,
        limit: view === 'recent' ? 20 : 100,
      })
      setItems(Array.isArray(data.items) ? data.items : [])
    } catch (e) {
      if (!silent) {
        setError(e?.message || 'Could not load files')
        setItems([])
      }
    } finally {
      if (!silent) setLoading(false)
    }
  }, [open, workspaceId, buildHeaders, statusFilter, q, view])

  useEffect(() => {
    if (!open) return
    void load()
  }, [open, load])

  const hasNonTerminal = items.some((item) => isNonTerminalFileStatus(item.status))

  useEffect(() => {
    if (!open || !workspaceId || !hasNonTerminal) return undefined
    const poller = createBoundedStatusPoller({
      shouldPoll: () => itemsRef.current.some((item) => isNonTerminalFileStatus(item.status)),
      refresh: () => load({ silent: true }),
    })
    poller.start()
    return () => poller.stop()
  }, [open, workspaceId, hasNonTerminal, load])

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  const runUpload = useCallback(
    async (fileList) => {
      // Copy immediately — clearing input.value can empty a live FileList.
      const files = Array.from(fileList || []).filter(Boolean)
      if (!files.length || !workspaceId || uploading || disabled) return
      setUploading(true)
      setUploadError(null)
      setUploadProgress(0)
      try {
        const headers = await buildHeaders()
        delete headers['Content-Type']
        delete headers['content-type']
        for (const file of files) {
          setUploadProgress(0)
          await uploadWorkspaceFile(workspaceId, file, headers, {
            onProgress: setUploadProgress,
          })
        }
        setUploadProgress(100)
        await load()
      } catch (e) {
        setUploadError(e?.message || 'Upload failed')
      } finally {
        setUploading(false)
      }
    },
    [workspaceId, uploading, disabled, buildHeaders, load]
  )

  const openPreview = useCallback(
    async (item) => {
      if (!workspaceId || !item) return
      setBusyId(item.id)
      setError(null)
      try {
        const headers = await buildHeaders()
        const meta = await getWorkspaceFile(workspaceId, item.id, headers, {
          includeTextPreview: true,
        })
        if (previewUrl) URL.revokeObjectURL(previewUrl)
        let url = null
        if (meta.preview_kind === 'image' || meta.preview_kind === 'pdf') {
          const blob = await fetchWorkspaceFileBlob(workspaceId, item.id, headers, {
            inline: true,
          })
          url = URL.createObjectURL(blob)
        }
        setPreviewUrl(url)
        setPreview(meta)
      } catch (e) {
        setError(e?.message || 'Preview failed')
      } finally {
        setBusyId(null)
      }
    },
    [workspaceId, buildHeaders, previewUrl]
  )

  const downloadFile = useCallback(
    async (item) => {
      if (!workspaceId || !item) return
      setBusyId(item.id)
      try {
        const headers = await buildHeaders()
        const blob = await fetchWorkspaceFileBlob(workspaceId, item.id, headers, {
          inline: false,
        })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = item.display_name || item.original_filename || 'download'
        a.click()
        URL.revokeObjectURL(url)
      } catch (e) {
        setError(e?.message || 'Download failed')
      } finally {
        setBusyId(null)
      }
    },
    [workspaceId, buildHeaders]
  )

  const retryFile = useCallback(
    async (item) => {
      if (!workspaceId || !item) return
      setBusyId(item.id)
      try {
        const headers = await buildHeaders()
        await retryWorkspaceFile(workspaceId, item.id, headers)
        await load()
      } catch (e) {
        setError(e?.message || 'Retry failed')
      } finally {
        setBusyId(null)
      }
    },
    [workspaceId, buildHeaders, load]
  )

  const removeFile = useCallback(
    async (item) => {
      if (!workspaceId || !item) return
      if (!window.confirm(`Delete “${item.display_name || item.original_filename}”?`)) return
      setBusyId(item.id)
      try {
        const headers = await buildHeaders()
        await deleteWorkspaceFile(workspaceId, item.id, headers)
        if (preview?.id === item.id) {
          setPreview(null)
          if (previewUrl) URL.revokeObjectURL(previewUrl)
          setPreviewUrl(null)
        }
        await load()
      } catch (e) {
        setError(e?.message || 'Delete failed')
      } finally {
        setBusyId(null)
      }
    },
    [workspaceId, buildHeaders, load, preview, previewUrl]
  )

  if (!open) return null

  return (
    <div className="files-overlay" role="dialog" aria-modal="true" aria-label="Workspace files">
      <button type="button" className="files-overlay__scrim" onClick={onClose} aria-label="Close files" />
      <div className="files-overlay__panel">
        <header className="files-overlay__header">
          <div>
            <h2 className="files-overlay__title">Files</h2>
            <p className="files-overlay__subtitle">
              {workspaceName || 'Workspace'}
              {!workspaceId ? ' — select a workspace first' : null}
            </p>
          </div>
          <button type="button" className="files-overlay__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {!workspaceId ? (
          <div className="files-empty">Select an active workspace/project to open the File Library.</div>
        ) : (
          <>
            <div className="files-toolbar">
              <nav className="files-tabs" aria-label="File views">
                {[
                  ['all', 'All files'],
                  ['recent', 'Recent'],
                  ['processing', 'Processing'],
                  ['failed', 'Failed'],
                ].map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    className={`files-tab${view === id ? ' files-tab--active' : ''}`}
                    onClick={() => setView(id)}
                  >
                    {label}
                  </button>
                ))}
              </nav>
              <form
                className="files-search"
                onSubmit={(e) => {
                  e.preventDefault()
                  setQ(searchInput.trim())
                }}
              >
                <input
                  type="search"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  placeholder="Search this workspace…"
                  aria-label="Search files"
                  disabled={disabled}
                />
                <button type="submit" disabled={disabled}>
                  Search
                </button>
              </form>
              <button
                type="button"
                className="files-upload-btn"
                disabled={disabled || uploading}
                onClick={() => fileInputRef.current?.click()}
              >
                {uploading ? `Uploading ${uploadProgress}%` : 'Upload'}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ACCEPT}
                className="files-file-input"
                tabIndex={-1}
                aria-hidden="true"
                disabled={disabled || uploading}
                onChange={(e) => {
                  const list = Array.from(e.target.files || [])
                  e.target.value = ''
                  void runUpload(list)
                }}
              />
            </div>

            <div
              className={`files-dropzone${dragActive ? ' files-dropzone--active' : ''}${
                uploading ? ' files-dropzone--busy' : ''
              }`}
              onDragEnter={(e) => {
                e.preventDefault()
                setDragActive(true)
              }}
              onDragOver={(e) => {
                e.preventDefault()
                setDragActive(true)
              }}
              onDragLeave={(e) => {
                e.preventDefault()
                setDragActive(false)
              }}
              onDrop={(e) => {
                e.preventDefault()
                setDragActive(false)
                void runUpload(e.dataTransfer?.files)
              }}
            >
              <p>Drag and drop files here, or use Upload.</p>
              <p className="files-dropzone__hint">
                PDF, DOCX, TXT, Markdown, CSV, XLSX, PPTX, images — max 50 MB
              </p>
              {uploading ? (
                <div className="files-progress" aria-label="Upload progress">
                  <div className="files-progress__bar" style={{ width: `${uploadProgress}%` }} />
                </div>
              ) : null}
              {uploadError ? <p className="files-error">{uploadError}</p> : null}
            </div>

            {error ? <p className="files-error">{error}</p> : null}

            <div className="files-body">
              <div className="files-list" aria-live="polite">
                {loading && !items.length ? <div className="files-empty">Loading…</div> : null}
                {!loading && !items.length ? (
                  <div className="files-empty">
                    No files yet. Upload a document to start this workspace library.
                  </div>
                ) : null}
                {items.map((item) => (
                  <article key={item.id} className="files-row">
                    <div className="files-row__main">
                      <div className="files-row__name" title={item.display_name}>
                        {item.display_name || item.original_filename}
                      </div>
                      <div className="files-row__meta">
                        <span>{fileExt(item.original_filename)}</span>
                        <span>{formatBytes(item.byte_size)}</span>
                        <span>{formatWhen(item.created_at)}</span>
                        {item.uploaded_by ? <span>{item.uploaded_by}</span> : null}
                      </div>
                      {item.status === 'failed' && item.failure_message ? (
                        <p className="files-row__fail">{item.failure_message}</p>
                      ) : null}
                      {item.status === 'ready' && item.failure_message ? (
                        <p className="files-row__warn">{item.failure_message}</p>
                      ) : null}
                    </div>
                    <div className="files-row__side">
                      <span
                        className={`files-status files-status--${String(item.status || '').toLowerCase()}`}
                      >
                        {fileStatusLabel(item.status)}
                      </span>
                      <div className="files-row__actions">
                        <button
                          type="button"
                          disabled={busyId === item.id}
                          onClick={() => void openPreview(item)}
                        >
                          Open
                        </button>
                        <button
                          type="button"
                          disabled={busyId === item.id}
                          onClick={() => void downloadFile(item)}
                        >
                          Download
                        </button>
                        {item.status === 'failed' ? (
                          <button
                            type="button"
                            disabled={busyId === item.id}
                            onClick={() => void retryFile(item)}
                          >
                            Retry
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="files-row__danger"
                          disabled={busyId === item.id}
                          onClick={() => void removeFile(item)}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>

              {preview ? (
                <aside className="files-preview" aria-label="File preview">
                  <div className="files-preview__head">
                    <h3>{preview.display_name}</h3>
                    <button
                      type="button"
                      onClick={() => {
                        setPreview(null)
                        if (previewUrl) URL.revokeObjectURL(previewUrl)
                        setPreviewUrl(null)
                      }}
                    >
                      Close
                    </button>
                  </div>
                  {preview.preview_kind === 'image' && previewUrl ? (
                    <img src={previewUrl} alt={preview.display_name} className="files-preview__img" />
                  ) : null}
                  {preview.preview_kind === 'pdf' && previewUrl ? (
                    <iframe title={preview.display_name} src={previewUrl} className="files-preview__frame" />
                  ) : null}
                  {preview.preview_kind === 'text' ? (
                    <pre className="files-preview__text">{preview.text_preview || '(empty)'}</pre>
                  ) : null}
                  {preview.preview_kind === 'download' ? (
                    <p className="files-empty">
                      Preview not available for this type. Use Download to open locally.
                    </p>
                  ) : null}
                </aside>
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

FileLibraryOverlay.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  workspaceId: PropTypes.string,
  workspaceName: PropTypes.string,
  buildHeaders: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
}

FileLibraryOverlay.defaultProps = {
  workspaceId: null,
  workspaceName: '',
  disabled: false,
}
