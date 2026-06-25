import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { BEN_API_BASE } from '../config.js'
import { fetchActiveAttention, fetchProjectKnowledgeFiles } from '../api/knowledge.js'
import './KnowledgeSidebar.css'

const HEAD_SECTIONS = [
  { key: 'code', label: 'Code Head', icon: '💻' },
  { key: 'documentation', label: 'Documentation Head', icon: '📄' },
  { key: 'history', label: 'History Head', icon: '🕒' },
]

function formatBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

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
  buildHeaders,
  disabled = false,
  attentionFocusRequest = null,
}) {
  const inputRef = useRef(null)
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState(null)
  const [focusData, setFocusData] = useState(null)
  const [focusLoading, setFocusLoading] = useState(false)
  const [focusError, setFocusError] = useState(null)

  const loadFiles = useCallback(async () => {
    if (!projectSlug || !buildHeaders) {
      setFiles([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const headers = await buildHeaders()
      const data = await fetchProjectKnowledgeFiles(projectSlug, headers)
      setFiles(data.files || [])
    } catch (e) {
      setError(e?.message || 'Could not load project knowledge files')
      setFiles([])
    } finally {
      setLoading(false)
    }
  }, [projectSlug, buildHeaders])

  useEffect(() => {
    void loadFiles()
  }, [loadFiles])

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
    if (!disabled && !uploading && !processing) {
      inputRef.current?.click()
    }
  }

  const handleFileChange = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || !projectSlug || !buildHeaders) return

    setUploading(true)
    setProcessing(false)
    setProgress(0)
    setError(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const headers = await buildHeaders()
      await axios.post(
        `${BEN_API_BASE}/api/projects/${encodeURIComponent(projectSlug)}/knowledge/upload-stream`,
        formData,
        {
          headers: {
            ...headers,
            'Content-Type': 'multipart/form-data',
          },
          onUploadProgress: (progressEvent) => {
            const total = progressEvent.total || file.size || 1
            const loaded = progressEvent.loaded || 0
            setProgress(Math.min(100, Math.round((loaded / total) * 100)))
          },
        }
      )
      setProgress(100)
      setProcessing(true)
      await loadFiles()
    } catch (e) {
      const detail = e?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : e?.message || 'Upload failed')
    } finally {
      setUploading(false)
      setProcessing(false)
    }
  }

  if (!projectSlug) {
    return null
  }

  const grouped = focusData?.grouped || {}
  const hasFocus = Boolean(focusData?.has_focus)
  const showEmptyFocus = !focusLoading && !focusError && !hasFocus

  return (
    <section className="knowledge-sidebar" aria-label="Project knowledge repository">
      <div className="knowledge-sidebar__upload">
        <p className="knowledge-sidebar__upload-label">📁 Upload Dataset / Logs</p>
        <p className="knowledge-sidebar__hint">
          Stream large datasets and logs (up to 500MB) into passive project storage for tool calls.
        </p>
        <input
          ref={inputRef}
          type="file"
          className="knowledge-sidebar__file-input"
          disabled={disabled || uploading || processing}
          onChange={(e) => void handleFileChange(e)}
        />
        <button
          type="button"
          className="knowledge-sidebar__upload-btn"
          disabled={disabled || uploading || processing}
          onClick={handlePickFile}
        >
          {uploading ? `Uploading… ${progress}%` : processing ? 'Processing…' : 'Choose file'}
        </button>
        {(uploading || progress === 100) && (
          <div className="knowledge-sidebar__progress" aria-label="Upload progress">
            <div
              className="knowledge-sidebar__progress-bar"
              style={{ width: `${progress}%` }}
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

      {error ? <p className="knowledge-sidebar__error">{error}</p> : null}

      <div className="knowledge-sidebar__list">
        <div className="knowledge-sidebar__list-header">
          <span>Repository files</span>
          <button
            type="button"
            className="knowledge-sidebar__refresh"
            disabled={disabled || loading || uploading}
            onClick={() => void loadFiles()}
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
            {files.map((file) => (
              <li key={file.id} className="knowledge-sidebar__file-row">
                <span className="knowledge-sidebar__file-name" title={file.filename}>
                  {file.filename}
                </span>
                <span className="knowledge-sidebar__file-meta">{formatBytes(file.size_bytes)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
