import { useEffect, useRef, useState } from 'react'
import { fetchActiveAttention, fetchProjectKnowledgeFiles } from '../api/knowledge.js'
import { FileLifecycleStatus } from './FileLifecycleStatus.jsx'
import { useWorkspaceFileInventory, workspaceFileInventory } from '../hooks/useWorkspaceFileInventory.jsx'
import { deriveFileStage, formatByteSize, processingPercent } from '../lib/fileStatus.js'
import './KnowledgeSidebar.css'

const HEAD_SECTIONS = [
  { key: 'code', label: 'Code Head', icon: '💻' },
  { key: 'documentation', label: 'Documentation Head', icon: '📄' },
  { key: 'history', label: 'History Head', icon: '🕒' },
]

function scoreTooltip(breakdown) {
  if (!breakdown) return ''
  return [
    `Semantic ${(breakdown.semantic_weighted * 100).toFixed(1)}%`,
    `Recency ${(breakdown.recency_weighted * 100).toFixed(1)}%`,
    `FTS ${(breakdown.fts_weighted * 100).toFixed(1)}%`,
  ].join(' · ')
}

function FocusItem({ item }) {
  const pct = Math.min(100, Math.max(0, Number(item.score_percent) || 0))
  return (
    <li className="knowledge-sidebar__focus-item">
      <div className="knowledge-sidebar__focus-row">
        <span className="knowledge-sidebar__focus-name" title={item.entity_name}>
          {item.entity_name}
        </span>
        <span
          className="knowledge-sidebar__focus-score"
          title={scoreTooltip(item.score_breakdown)}
        >
          {pct.toFixed(1)}%
        </span>
      </div>
      <div className="knowledge-sidebar__focus-bar" aria-hidden="true">
        <div className="knowledge-sidebar__focus-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="knowledge-sidebar__focus-meta">{item.updated_relative}</span>
    </li>
  )
}

export function KnowledgeSidebar({
  projectSlug,
  workspaceId = null,
  buildHeaders,
  disabled = false,
  attentionFocusRequest = null,
  onOpenFileLibrary = null,
}) {
  const inputRef = useRef(null)
  const inventory = useWorkspaceFileInventory()
  const [legacyFiles, setLegacyFiles] = useState([])
  const [legacyLoading, setLegacyLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)
  const [focusData, setFocusData] = useState(null)
  const [focusLoading, setFocusLoading] = useState(false)
  const [focusError, setFocusError] = useState(null)
  const usingInventory = Boolean(workspaceId)
  const files = usingInventory ? inventory.rows : legacyFiles
  const loading = usingInventory ? inventory.loading : legacyLoading
  const activeUpload = (inventory.uploads || []).find((item) => item.phase === 'uploading')
  const progress = processingPercent(null, activeUpload)

  useEffect(() => {
    if (workspaceId || !projectSlug || !buildHeaders) {
      if (!workspaceId) setLegacyFiles([])
      return
    }
    let cancelled = false
    setLegacyLoading(true)
    setError(null)
    void (async () => {
      try {
        const headers = await buildHeaders()
        const data = await fetchProjectKnowledgeFiles(projectSlug, headers)
        if (cancelled) return
        setLegacyFiles(
          (data.files || []).map((file) => ({
            id: file.id,
            display_name: file.filename || file.name,
            original_filename: file.filename || file.name,
            byte_size: file.size_bytes ?? file.size,
            status: file.status,
          }))
        )
      } catch (e) {
        if (!cancelled) {
          setError(e?.message || 'Could not load project knowledge files')
          setLegacyFiles([])
        }
      } finally {
        if (!cancelled) setLegacyLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [projectSlug, workspaceId, buildHeaders])

  useEffect(() => {
    const req = attentionFocusRequest
    if (!req?.query || !req?.threadId || !projectSlug || !buildHeaders) {
      return
    }

    let cancelled = false
    setFocusLoading(true)
    setFocusError(null)

    void (async () => {
      try {
        const headers = await buildHeaders()
        const data = await fetchActiveAttention(
          projectSlug,
          req.threadId,
          req.query,
          headers
        )
        if (!cancelled) {
          setFocusData(data)
        }
      } catch (e) {
        if (!cancelled) {
          setFocusError(e?.message || 'Could not load active context focus')
          setFocusData(null)
        }
      } finally {
        if (!cancelled) {
          setFocusLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [attentionFocusRequest, projectSlug, buildHeaders])

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

  const grouped = focusData?.grouped || {}
  const hasFocus = Boolean(focusData?.has_focus)
  const showEmptyFocus = !focusLoading && !focusError && !hasFocus

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
          Hybrid attention weights (semantic + recency + FTS5) for the current prompt.
        </p>
        {focusLoading ? (
          <p className="knowledge-sidebar__hint">Analyzing context…</p>
        ) : focusError ? (
          <p className="knowledge-sidebar__error">{focusError}</p>
        ) : showEmptyFocus ? (
          <p className="knowledge-sidebar__focus-empty">No active focus</p>
        ) : (
          HEAD_SECTIONS.map((section) => {
            const items = grouped[section.key] || []
            if (!items.length) return null
            return (
              <div key={section.key} className="knowledge-sidebar__focus-group">
                <p className="knowledge-sidebar__focus-group-label">
                  {section.icon} {section.label}
                </p>
                <ul className="knowledge-sidebar__focus-list">
                  {items.map((item) => (
                    <FocusItem key={`${section.key}-${item.entity_name}`} item={item} />
                  ))}
                </ul>
              </div>
            )
          })
        )}
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
                    className={`knowledge-sidebar__file-status knowledge-sidebar__file-status--${deriveFileStage(file, { upload: file.upload })}`}
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
