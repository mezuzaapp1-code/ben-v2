import PropTypes from 'prop-types'
import { useCallback, useEffect, useRef, useState } from 'react'
import { acquirePersistentHeaders } from '../api/benHeaders.js'
import { fetchProjects } from '../api/projects.js'
import { projectLibraryActiveCopy } from '../lib/activeProject.js'
import {
  PROJECT_LIBRARY_DEFAULT_LIMIT,
  PROJECT_LIBRARY_MAX_ITEMS,
  PROJECT_LIBRARY_REOPEN_RESETS,
  PROJECT_LIBRARY_SEARCH_DEBOUNCE_MS,
  PROJECT_LIBRARY_SEARCH_MAX,
  applyProjectPage,
  normalizeProjectSearchQuery,
  projectLibraryEmptyMessage,
} from '../lib/projectLibrary.js'
import './ProjectLibraryOverlay.css'

function formatWhen(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export function ProjectLibraryNavTrigger({ onOpen, active = false, disabled = false }) {
  return (
    <button
      type="button"
      className={`projects-nav-trigger${active ? ' projects-nav-trigger--active' : ''}`}
      onClick={onOpen}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-current={active ? 'page' : undefined}
    >
      <span>Projects</span>
      <span className="projects-nav-trigger__chevron" aria-hidden="true">
        ▸
      </span>
    </button>
  )
}

ProjectLibraryNavTrigger.propTypes = {
  onOpen: PropTypes.func.isRequired,
  active: PropTypes.bool,
  disabled: PropTypes.bool,
}

ProjectLibraryNavTrigger.defaultProps = {
  active: false,
  disabled: false,
}

/**
 * Org Project Library — one bounded page at a time, Load more continuation.
 */
export function ProjectLibraryOverlay({
  open,
  onClose,
  tenantId = null,
  activeProjectId = null,
  activeProjectName = '',
  buildHeaders,
  disabled = false,
  canCreateProject = false,
  onNewProject,
  onOpenProject,
}) {
  const [pageState, setPageState] = useState({ tenantId, query: '', items: [], nextCursor: null })
  const [searchInput, setSearchInput] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)
  const requestSeq = useRef(0)
  const loadPageRef = useRef(null)
  const searching = Boolean(debouncedQuery)
  const items =
    pageState.tenantId === tenantId && (pageState.query || '') === debouncedQuery
      ? pageState.items
      : []
  const nextCursor =
    pageState.tenantId === tenantId && (pageState.query || '') === debouncedQuery
      ? pageState.nextCursor
      : null
  const tenantMismatch = pageState.tenantId !== tenantId
  const queryMismatch = (pageState.query || '') !== debouncedQuery

  const loadPage = useCallback(
    async ({ cursor = null, append = false } = {}) => {
      if (!buildHeaders) {
        setPageState({ tenantId, query: debouncedQuery, items: [], nextCursor: null })
        setError(null)
        setLoading(false)
        setLoadingMore(false)
        return
      }
      const seq = ++requestSeq.current
      if (append) setLoadingMore(true)
      else {
        setLoading(true)
        setError(null)
      }
      try {
        const headers = await acquirePersistentHeaders(buildHeaders)
        const data = await fetchProjects(headers, {
          limit: PROJECT_LIBRARY_DEFAULT_LIMIT,
          cursor,
          query: debouncedQuery || undefined,
        })
        if (seq !== requestSeq.current) return
        setPageState((prev) => {
          const applied = applyProjectPage(
            {
              items: append && prev.tenantId === tenantId && (prev.query || '') === debouncedQuery
                ? prev.items
                : [],
              nextCursor: null,
            },
            data,
            { maxItems: PROJECT_LIBRARY_MAX_ITEMS }
          )
          return {
            tenantId,
            query: debouncedQuery,
            items: applied.items,
            nextCursor: applied.nextCursor,
          }
        })
        setError(null)
      } catch (e) {
        if (seq !== requestSeq.current) return
        setError(e?.message || 'Could not load projects.')
        if (!append) setPageState({ tenantId, query: debouncedQuery, items: [], nextCursor: null })
      } finally {
        if (seq === requestSeq.current) {
          setLoading(false)
          setLoadingMore(false)
        }
      }
    },
    [buildHeaders, tenantId, debouncedQuery]
  )
  loadPageRef.current = loadPage

  useEffect(() => {
    const needle = normalizeProjectSearchQuery(searchInput)
    const timer = window.setTimeout(() => {
      setDebouncedQuery(needle)
    }, PROJECT_LIBRARY_SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    setSearchInput('')
    setDebouncedQuery('')
  }, [open, tenantId])

  useEffect(() => {
    if (!open || !PROJECT_LIBRARY_REOPEN_RESETS) return
    requestSeq.current += 1
    setPageState({ tenantId, query: debouncedQuery, items: [], nextCursor: null })
    setError(null)
    setLoading(true)
    void loadPageRef.current?.({ append: false })
  }, [open, tenantId, debouncedQuery])

  const listLoading = loading || tenantMismatch || queryMismatch
  const emptyCopy = projectLibraryEmptyMessage({
    signedIn: Boolean(buildHeaders),
    loading: listLoading,
    error: tenantMismatch ? null : error,
    itemCount: items.length,
    searching,
  })

  if (!open) return null

  const showMore = Boolean(nextCursor) && items.length < PROJECT_LIBRARY_MAX_ITEMS && !loading

  return (
    <div className="projects-overlay" role="dialog" aria-modal="true" aria-label="Projects">
      <button type="button" className="projects-overlay__scrim" onClick={onClose} aria-label="Close projects" />
      <div className="projects-overlay__panel">
        <header className="projects-overlay__header">
          <div>
            <h2 className="projects-overlay__title">Projects</h2>
            <p className="projects-overlay__subtitle">
              {projectLibraryActiveCopy({ id: activeProjectId, name: activeProjectName })}
            </p>
          </div>
          <button type="button" className="projects-overlay__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="projects-toolbar">
          <label className="projects-search">
            <span className="projects-search__label">Search projects</span>
            <input
              type="search"
              className="projects-search__input"
              value={searchInput}
              maxLength={PROJECT_LIBRARY_SEARCH_MAX}
              placeholder="Search projects..."
              onChange={(event) => setSearchInput(event.target.value)}
              disabled={!buildHeaders || disabled}
              autoComplete="off"
              spellCheck="false"
            />
          </label>
          <button
            type="button"
            className="projects-new-btn"
            disabled={disabled || !canCreateProject}
            onClick={() => onNewProject?.()}
          >
            + New project
          </button>
        </div>

        {error && !tenantMismatch ? (
          <p className="projects-error">
            {error}{' '}
            <button type="button" onClick={() => void loadPage({ append: false })}>
              Retry
            </button>
          </p>
        ) : null}

        <div className="projects-body">
          {listLoading ? (
            <p className="projects-status">{searching ? 'Searching…' : 'Loading projects…'}</p>
          ) : null}
          {!listLoading && emptyCopy ? <div className="projects-empty">{emptyCopy}</div> : null}
          {!listLoading && items.length > 0 ? (
            <ul className="projects-list">
              {items.map((project) => {
                const isActive = String(project.id) === String(activeProjectId || '')
                const fileCount = Number(project.file_count) || 0
                return (
                  <li
                    key={project.id}
                    className={`projects-row${isActive ? ' projects-row--active' : ''}`}
                  >
                    <div className="projects-row__main">
                      <span className="projects-row__name">
                        {project.name || 'Untitled project'}
                        {isActive ? <span className="projects-row__badge">Active</span> : null}
                      </span>
                      <span className="projects-row__meta">
                        {project.status || 'active'}
                        {' · '}
                        {fileCount} {fileCount === 1 ? 'file' : 'files'}
                        {' · '}
                        {formatWhen(project.updated_at)}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="projects-row__open"
                      disabled={disabled}
                      onClick={() => onOpenProject?.(project)}
                    >
                      Open
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : null}
          {showMore ? (
            <div className="projects-more">
              <button
                type="button"
                disabled={disabled || loadingMore}
                onClick={() => void loadPage({ cursor: nextCursor, append: true })}
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

ProjectLibraryOverlay.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  tenantId: PropTypes.string,
  activeProjectId: PropTypes.string,
  activeProjectName: PropTypes.string,
  buildHeaders: PropTypes.func,
  disabled: PropTypes.bool,
  canCreateProject: PropTypes.bool,
  onNewProject: PropTypes.func,
  onOpenProject: PropTypes.func,
}

ProjectLibraryOverlay.defaultProps = {
  tenantId: null,
  activeProjectId: null,
  activeProjectName: '',
  buildHeaders: null,
  disabled: false,
  canCreateProject: false,
  onNewProject: null,
  onOpenProject: null,
}
