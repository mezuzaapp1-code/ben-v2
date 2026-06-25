import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import PropTypes from 'prop-types'
import {
  connectProjectRepository,
  fetchProjectRepositories,
  REPOSITORY_UPLOAD_MIME_TYPES,
  toggleProjectRepository,
  uploadRepositoryFileChunked,
} from '../api/repositories.js'
import { getDiscoveryChannel } from '../data/discoveryCatalog.js'
import {
  formatRepositorySourceType,
  formatUploadBytes,
  groupRepositories,
  isRepositoryUploadMimeAllowed,
} from '../lib/repositoryDashboard.js'
import './ProjectRepositoriesDashboard.css'

/** @typedef {import('../lib/repositoryDashboard.js').RepositoryRecord} RepositoryRecord */

const GROUP_SECTIONS = Object.freeze([
  { id: 'local', title: 'Local document vault', empty: 'No local vault connected.' },
  { id: 'cloud', title: 'Cloud & live streams', empty: 'No cloud repositories linked.' },
  { id: 'security', title: 'Security channels', empty: 'No security integrations configured.' },
])

const ACCEPT_ATTR = '.pdf,.epub,application/pdf,application/epub+zip'

/**
 * @param {RepositoryRecord[]} repositories
 */
function findActiveLocalRepository(repositories) {
  return (
    repositories.find(
      (repo) =>
        repo.status === 'active' &&
        (repo.source_type === 'local' || repo.catalog_key === 'repo-local')
    ) ?? null
  )
}

function UploadProgressPanel({ uploadState }) {
  if (!uploadState) {
    return (
      <div className="repo-dashboard__progress-shell" aria-hidden="true">
        <p className="text-2xs text-ben-muted">Progress will appear here during ingestion.</p>
      </div>
    )
  }

  const { filename, percent, bytes } = uploadState
  return (
    <div className="repo-dashboard__progress-shell" aria-live="polite">
      <div className="mb-1 flex items-center justify-between gap-2 text-2xs">
        <span className="truncate font-medium text-ben-text" title={filename}>
          {filename}
        </span>
        <span className="shrink-0 tabular-nums text-ben-muted">{percent}%</span>
      </div>
      <div className="repo-dashboard__progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <div className="repo-dashboard__progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-1 text-2xs text-ben-muted">{formatUploadBytes(bytes)} · chunked stream</p>
    </div>
  )
}

UploadProgressPanel.propTypes = {
  uploadState: PropTypes.shape({
    filename: PropTypes.string.isRequired,
    percent: PropTypes.number.isRequired,
    bytes: PropTypes.number.isRequired,
  }),
}

UploadProgressPanel.defaultProps = {
  uploadState: null,
}

function RepositoryRow({ repository, toggling, disabled, onDisconnect }) {
  const isActive = repository.status === 'active'
  const catalogKey = String(repository.catalog_key || repository.source_metadata?.catalog_key || '')
  const sourceLabel = formatRepositorySourceType(repository.source_type)

  return (
    <li
      className={`repo-dashboard__row${toggling ? ' repo-dashboard__row--pending' : ''}`}
      aria-busy={toggling}
    >
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="truncate text-sm font-semibold text-ben-text">{repository.name}</h4>
          <span
            className={`repo-dashboard__status ${
              isActive ? 'repo-dashboard__status--active' : 'repo-dashboard__status--disconnected'
            }`}
          >
            {isActive ? 'Active' : 'Disconnected'}
          </span>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-2xs text-ben-muted">
          <dt className="font-medium text-ben-text/70">Source type</dt>
          <dd>{sourceLabel}</dd>
          {catalogKey ? (
            <>
              <dt className="font-medium text-ben-text/70">Catalog</dt>
              <dd className="truncate font-mono">{catalogKey}</dd>
            </>
          ) : null}
        </dl>
      </div>
      <button
        type="button"
        className="repo-dashboard__action"
        disabled={disabled || toggling || !isActive}
        aria-busy={toggling}
        onClick={() => onDisconnect(repository)}
      >
        {toggling ? 'Scrubbing…' : 'Disconnect'}
      </button>
    </li>
  )
}

RepositoryRow.propTypes = {
  repository: PropTypes.shape({
    id: PropTypes.number.isRequired,
    name: PropTypes.string.isRequired,
    source_type: PropTypes.string.isRequired,
    status: PropTypes.string.isRequired,
    catalog_key: PropTypes.string,
    source_metadata: PropTypes.object,
  }).isRequired,
  toggling: PropTypes.bool.isRequired,
  disabled: PropTypes.bool.isRequired,
  onDisconnect: PropTypes.func.isRequired,
}

export function ProjectRepositoriesDashboard({ projectSlug, buildHeaders, disabled = false }) {
  /** @type {[RepositoryRecord[], Function]} */
  const [repositories, setRepositories] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(null)
  const [dragActive, setDragActive] = useState(false)
  const [uploadState, setUploadState] = useState(null)
  const [uploadError, setUploadError] = useState(null)
  const [uploadBusy, setUploadBusy] = useState(false)
  /** @type {[Set<number>, Function]} */
  const [pendingToggleIds, setPendingToggleIds] = useState(() => new Set())
  const [recentUploads, setRecentUploads] = useState([])

  const fileInputRef = useRef(null)
  const abortRef = useRef(null)

  const grouped = useMemo(() => groupRepositories(repositories), [repositories])
  const activeLocalRepository = useMemo(
    () => findActiveLocalRepository(repositories),
    [repositories]
  )

  const loadRepositories = useCallback(async () => {
    if (!projectSlug || !buildHeaders) {
      setRepositories([])
      return
    }
    setLoading(true)
    setLoadError(null)
    try {
      const headers = await buildHeaders()
      const data = await fetchProjectRepositories(projectSlug, headers)
      setRepositories(Array.isArray(data.repositories) ? data.repositories : [])
    } catch (error) {
      setRepositories([])
      setLoadError(error?.message || 'Could not load repositories')
    } finally {
      setLoading(false)
    }
  }, [projectSlug, buildHeaders])

  useEffect(() => {
    void loadRepositories()
  }, [loadRepositories])

  const ensureLocalRepository = useCallback(async () => {
    const existing = findActiveLocalRepository(repositories)
    if (existing) return existing

    const channel = getDiscoveryChannel('repo-local')
    if (!channel || !projectSlug || !buildHeaders) {
      throw new Error('Enable the Local Repository Vault in Discovery Center before uploading.')
    }

    const headers = await buildHeaders()
    const connected = await connectProjectRepository(projectSlug, channel.connect, headers)
    const repo = connected.repository
    if (!repo?.id) {
      throw new Error('Local repository connect failed')
    }
    setRepositories((prev) => {
      const withoutDup = prev.filter((row) => row.id !== repo.id)
      return [repo, ...withoutDup]
    })
    return repo
  }, [buildHeaders, projectSlug, repositories])

  const runUpload = useCallback(
    async (file) => {
      if (!projectSlug || !buildHeaders || disabled || uploadBusy) return
      if (!isRepositoryUploadMimeAllowed(file)) {
        setUploadError('Only PDF and EPUB documents are supported.')
        return
      }

      setUploadError(null)
      setUploadBusy(true)
      setUploadState({ filename: file.name, percent: 0, bytes: file.size })

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const localRepo = await ensureLocalRepository()
        const headers = await buildHeaders()
        const result = await uploadRepositoryFileChunked(
          projectSlug,
          localRepo.id,
          file,
          headers,
          {
            signal: controller.signal,
            onProgress: (percent) => {
              setUploadState({ filename: file.name, percent, bytes: file.size })
            },
          }
        )

        const uploaded = result?.file
        if (uploaded) {
          setRecentUploads((prev) => [
            {
              id: uploaded.id,
              filename: uploaded.filename,
              size_bytes: uploaded.size_bytes,
              repository_id: localRepo.id,
              uploaded_at: uploaded.uploaded_at,
            },
            ...prev.filter((row) => row.id !== uploaded.id),
          ])
        }
      } catch (error) {
        setUploadError(error?.message || 'Upload failed')
      } finally {
        abortRef.current = null
        setUploadBusy(false)
        window.setTimeout(() => setUploadState(null), 1200)
      }
    },
    [buildHeaders, disabled, ensureLocalRepository, projectSlug, uploadBusy]
  )

  const handleFiles = useCallback(
    (fileList) => {
      const files = Array.from(fileList || [])
      if (!files.length) return
      void runUpload(files[0])
    },
    [runUpload]
  )

  const handleDisconnect = useCallback(
    async (repository) => {
      if (!projectSlug || !buildHeaders || disabled) return
      const repoId = Number(repository.id)
      setPendingToggleIds((prev) => new Set(prev).add(repoId))
      try {
        const headers = await buildHeaders()
        await toggleProjectRepository(projectSlug, repoId, headers)
        await loadRepositories()
      } catch (error) {
        setLoadError(error?.message || 'Disconnect failed')
      } finally {
        setPendingToggleIds((prev) => {
          const next = new Set(prev)
          next.delete(repoId)
          return next
        })
      }
    },
    [buildHeaders, disabled, loadRepositories, projectSlug]
  )

  const onDragEnter = (event) => {
    event.preventDefault()
    event.stopPropagation()
    if (disabled || uploadBusy) return
    setDragActive(true)
  }

  const onDragOver = (event) => {
    event.preventDefault()
    event.stopPropagation()
    if (disabled || uploadBusy) return
    setDragActive(true)
  }

  const onDragLeave = (event) => {
    event.preventDefault()
    event.stopPropagation()
    if (event.currentTarget.contains(event.relatedTarget)) return
    setDragActive(false)
  }

  const onDrop = (event) => {
    event.preventDefault()
    event.stopPropagation()
    setDragActive(false)
    if (disabled || uploadBusy) return
    handleFiles(event.dataTransfer?.files)
  }

  if (!projectSlug) {
    return null
  }

  return (
    <section className="repo-dashboard" aria-label="Project repositories dashboard">
      <header className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold tracking-tight text-ben-text">Repositories</h2>
          <p className="text-2xs text-ben-muted">
            Chunked PDF/EPUB ingestion and virtual source management.
          </p>
        </div>
        {loading ? (
          <span className="discovery-status-pill bg-white/5 text-ben-muted" aria-live="polite">
            SYNCING
          </span>
        ) : null}
      </header>

      <div
        className={[
          'repo-dashboard__dropzone',
          dragActive ? 'repo-dashboard__dropzone--active' : '',
          uploadBusy ? 'repo-dashboard__dropzone--busy' : '',
        ].join(' ')}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
        aria-disabled={disabled || uploadBusy}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            if (!disabled && !uploadBusy) fileInputRef.current?.click()
          }
        }}
        onClick={() => {
          if (!disabled && !uploadBusy) fileInputRef.current?.click()
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT_ATTR}
          className="repo-dashboard__file-input"
          disabled={disabled || uploadBusy}
          onChange={(event) => {
            handleFiles(event.target.files)
            event.target.value = ''
          }}
        />
        <p className="text-sm font-medium text-ben-text">Drop PDF or EPUB here</p>
        <p className="text-2xs text-ben-muted">
          1MB chunked stream · up to 500MB per file
          {activeLocalRepository ? '' : ' · local vault auto-links on first upload'}
        </p>
        <p className="text-2xs text-ben-muted/80">
          Accepted: {REPOSITORY_UPLOAD_MIME_TYPES.join(', ')}
        </p>
      </div>

      <UploadProgressPanel uploadState={uploadState} />
      {uploadError ? <p className="mt-2 text-2xs text-red-400">{uploadError}</p> : null}

      {recentUploads.length > 0 ? (
        <div className="mt-4 space-y-2">
          <h3 className="text-2xs font-semibold uppercase tracking-wider text-ben-muted">
            Recent uploads
          </h3>
          <ul className="space-y-2">
            {recentUploads.slice(0, 5).map((file) => (
              <li
                key={file.id}
                className="repo-dashboard__row"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ben-text">{file.filename}</p>
                  <p className="text-2xs text-ben-muted">
                    {formatUploadBytes(file.size_bytes)} · local vault
                  </p>
                </div>
                <span className="repo-dashboard__status repo-dashboard__status--active">Ingested</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {loadError ? <p className="mt-3 text-2xs text-red-400">{loadError}</p> : null}

      <div className="mt-4 space-y-4">
        {GROUP_SECTIONS.map((section) => {
          const rows = grouped[section.id]
          return (
            <div key={section.id}>
              <div className="mb-2 flex items-baseline justify-between gap-2">
                <h3 className="text-2xs font-semibold uppercase tracking-wider text-ben-muted">
                  {section.title}
                </h3>
                <span className="text-2xs tabular-nums text-ben-muted">{rows.length} linked</span>
              </div>
              {rows.length === 0 ? (
                <p className="min-h-[2.5rem] rounded-lg border border-dashed border-ben-border/70 px-3 py-2 text-2xs text-ben-muted">
                  {section.empty}
                </p>
              ) : (
                <ul className="space-y-2">
                  {rows.map((repository) => (
                    <RepositoryRow
                      key={repository.id}
                      repository={repository}
                      toggling={pendingToggleIds.has(Number(repository.id))}
                      disabled={disabled || loading}
                      onDisconnect={handleDisconnect}
                    />
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

ProjectRepositoriesDashboard.propTypes = {
  projectSlug: PropTypes.string,
  buildHeaders: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
}

ProjectRepositoriesDashboard.defaultProps = {
  projectSlug: null,
  disabled: false,
}
