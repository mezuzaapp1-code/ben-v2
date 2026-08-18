/**
 * Single workspace-file inventory + poller shared by Sidebar, File Library,
 * and composer attachments. One in-flight list request at a time.
 */
import { acquirePersistentHeaders, isAuthTokenUnavailable } from '../api/benHeaders.js'
import {
  FILE_STATUS_POLL_MS,
  createBoundedStatusPoller,
  isNonTerminalFile,
  mergeFileInventory,
} from './fileStatus.js'

function emptySnapshot(workspaceId = null) {
  return {
    workspaceId,
    files: [],
    uploads: [],
    rows: [],
    loading: false,
    error: null,
  }
}

function nextLocalId() {
  return `upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function createWorkspaceFileInventory({
  listFiles,
  uploadFile,
  pollerFactory = createBoundedStatusPoller,
  intervalMs = FILE_STATUS_POLL_MS,
} = {}) {
  let workspaceId = null
  let buildHeaders = null
  let files = []
  let uploads = []
  let loading = false
  let error = null
  let snapshot = emptySnapshot()
  const listeners = new Set()
  let poller = null
  let loadPromise = null
  let generation = 0
  let authRetry = false

  function emit() {
    snapshot = {
      workspaceId,
      files,
      uploads,
      rows: mergeFileInventory(files, uploads),
      loading,
      error,
    }
    listeners.forEach((fn) => {
      try {
        fn()
      } catch {
        /* ignore subscriber errors */
      }
    })
  }

  function getSnapshot() {
    return snapshot
  }

  function subscribe(fn) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  }

  function shouldPoll() {
    if (!workspaceId || !buildHeaders) return false
    if (authRetry) return true
    if (uploads.some((u) => u.phase === 'uploading')) return true
    return files.some((file) => isNonTerminalFile(file))
  }

  function stopPoller() {
    if (!poller) return
    poller.stop()
    poller = null
  }

  function ensurePoller() {
    if (!shouldPoll()) {
      stopPoller()
      return
    }
    if (poller && !poller.stopped) return
    stopPoller()
    poller = pollerFactory({
      shouldPoll,
      refresh: () => load({ silent: true }),
      intervalMs,
      immediate: false,
    })
    poller.start()
  }

  async function load({ silent = false } = {}) {
    if (!workspaceId || !buildHeaders) {
      files = []
      if (!silent) loading = false
      error = null
      emit()
      return { items: [] }
    }
    if (loadPromise) return loadPromise
    const gen = generation
    if (!silent) {
      loading = true
      error = null
      emit()
    }
    loadPromise = (async () => {
      if (typeof listFiles !== 'function') return { items: [] }
      const headers = await acquirePersistentHeaders(buildHeaders, {
        attempts: silent ? 2 : 4,
        delayMs: silent ? 0 : 50,
      })
      return listFiles(workspaceId, headers, { limit: 100 })
    })()
    try {
      const data = await loadPromise
      if (gen !== generation) return data
      files = Array.isArray(data?.items) ? data.items : []
      error = null
      authRetry = false
      return data
    } catch (e) {
      if (gen !== generation) return { items: files }
      if (isAuthTokenUnavailable(e)) {
        authRetry = Boolean(workspaceId && buildHeaders)
        return { items: files, skipped: 'auth' }
      }
      authRetry = false
      if (!silent) {
        error = e?.message || 'Could not load workspace files'
        files = []
      }
      return { items: files }
    } finally {
      loadPromise = null
      if (gen === generation) {
        loading = false
        emit()
        ensurePoller()
      }
    }
  }

  function configure({ workspaceId: nextId = null, buildHeaders: nextHeaders = null } = {}) {
    const id = nextId || null
    const scopeChanged = id !== workspaceId
    const signedInChanged = Boolean(buildHeaders) !== Boolean(nextHeaders)
    buildHeaders = nextHeaders
    workspaceId = id
    if (!scopeChanged && !signedInChanged) {
      if (id && nextHeaders) ensurePoller()
      return
    }
    generation += 1
    stopPoller()
    files = []
    uploads = []
    authRetry = false
    loading = false
    error = null
    emit()
    if (id && nextHeaders) {
      void load({ silent: false })
    }
  }

  function beginUpload(file) {
    const localId = nextLocalId()
    const total = Number(file?.size) > 0 ? Number(file.size) : null
    uploads = [
      ...uploads,
      {
        localId,
        name: file?.name || 'upload',
        byteSize: total,
        loaded: 0,
        total,
        percent: total != null ? 0 : null,
        phase: 'uploading',
        fileId: null,
        error: null,
      },
    ]
    emit()
    return localId
  }

  function updateUpload(localId, patch) {
    uploads = uploads.map((u) => (u.localId === localId ? { ...u, ...patch } : u))
    emit()
  }

  function removeUpload(localId) {
    uploads = uploads.filter((u) => u.localId !== localId)
    emit()
  }

  function upsertFile(payload) {
    if (!payload?.id) return
    const id = String(payload.id)
    const idx = files.findIndex((f) => String(f.id) === id)
    if (idx >= 0) {
      files = files.map((f, i) => (i === idx ? { ...f, ...payload } : f))
    } else {
      files = [payload, ...files]
    }
    emit()
    ensurePoller()
  }

  async function uploadFileToWorkspace(file, { sourceChatId, localId } = {}) {
    if (!workspaceId || !buildHeaders) {
      throw new Error('Select an active workspace/project before uploading.')
    }
    const id = localId || beginUpload(file)
    try {
      const headers = await acquirePersistentHeaders(buildHeaders)
      delete headers['Content-Type']
      delete headers['content-type']
      if (typeof uploadFile !== 'function') {
        throw new Error('Upload is not configured')
      }
      const result = await uploadFile(workspaceId, file, headers, {
        sourceChatId,
        onProgress: (progress) => {
          updateUpload(id, normalizeProgress(progress, file))
        },
      })
      updateUpload(id, {
        phase: 'done',
        fileId: result?.id || null,
        loaded: Number(file?.size) > 0 ? Number(file.size) : undefined,
        total: Number(file?.size) > 0 ? Number(file.size) : undefined,
        percent: Number(file?.size) > 0 ? 100 : null,
      })
      if (result) upsertFile(result)
      removeUpload(id)
      void load({ silent: true })
      return { localId: id, result }
    } catch (e) {
      updateUpload(id, {
        phase: 'failed',
        error: e?.message || 'Upload failed',
      })
      throw e
    }
  }

  return {
    subscribe,
    getSnapshot,
    configure,
    load,
    refresh: () => load({ silent: true }),
    beginUpload,
    updateUpload,
    removeUpload,
    upsertFile,
    uploadFile: uploadFileToWorkspace,
    ensurePoller,
    stopPoller,
    get poller() {
      return poller
    },
  }
}

export function normalizeProgress(progress, file) {
  if (progress == null) return {}
  if (typeof progress === 'number') {
    const total = Number(file?.size) > 0 ? Number(file.size) : null
    const percent = Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : null
    const loaded = percent != null && total != null ? Math.round((percent / 100) * total) : null
    return { loaded, total, percent, phase: 'uploading' }
  }
  const total =
    Number(progress.total) > 0
      ? Number(progress.total)
      : Number(file?.size) > 0
        ? Number(file.size)
        : null
  const loaded = Number.isFinite(Number(progress.loaded)) ? Number(progress.loaded) : null
  let percent = null
  if (progress.percent != null && Number.isFinite(Number(progress.percent))) {
    percent = Math.round(Number(progress.percent))
  }
  if (percent == null && loaded != null && total != null && total > 0) {
    percent = Math.min(100, Math.round((loaded / total) * 100))
  }
  if (percent != null) percent = Math.max(0, Math.min(100, percent))
  return {
    loaded,
    total,
    percent,
    phase: 'uploading',
  }
}
