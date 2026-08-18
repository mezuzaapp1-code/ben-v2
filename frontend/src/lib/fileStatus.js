/** Honest file lifecycle + used-files helpers. Backend status is source of truth. */

export const FILE_STATUS_POLL_MS = 3000

export const FILE_STAGES = {
  uploading: 'uploading',
  queued: 'queued',
  extracting: 'extracting',
  indexing: 'indexing',
  ready: 'ready',
  failed: 'failed',
}

export const FILE_STAGE_LABELS = {
  uploading: 'Uploading',
  queued: 'Queued',
  extracting: 'Extracting',
  indexing: 'Indexing',
  ready: 'Ready',
  failed: 'Failed',
}

/** @deprecated Prefer FILE_STAGE_LABELS / fileStageLabel. Kept for older copy checks. */
export const FILE_STATUS_LABELS = {
  queued: FILE_STAGE_LABELS.queued,
  uploaded: FILE_STAGE_LABELS.queued,
  processing: FILE_STAGE_LABELS.extracting,
  extracting: FILE_STAGE_LABELS.extracting,
  indexing: FILE_STAGE_LABELS.indexing,
  ready: FILE_STAGE_LABELS.ready,
  failed: FILE_STAGE_LABELS.failed,
}

export function normalizeFileStatus(status) {
  return String(status || '')
    .trim()
    .toLowerCase()
}

function asInt(value) {
  if (value == null || value === '') return null
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return Math.trunc(n)
}

/**
 * Page progress only when both sides are real. Never invent X or Y.
 * @returns {{ x: number, y: number } | null}
 */
export function pageProgress(file) {
  if (!file || typeof file !== 'object') return null
  const y = asInt(file.page_count)
  const x = asInt(file.pages_extracted)
  if (y == null || x == null || y <= 0 || x < 0) return null
  return { x, y }
}

export function deriveFileStage(file, { upload } = {}) {
  if (upload && upload.phase === 'uploading') return FILE_STAGES.uploading
  if (upload && upload.phase === 'failed' && !file?.id && !file?.status) {
    return FILE_STAGES.failed
  }

  const status = normalizeFileStatus(file?.status)
  const extraction = normalizeFileStatus(file?.extraction_status) || 'pending'
  const index = normalizeFileStatus(file?.index_status) || 'not_indexed'
  const job = normalizeFileStatus(file?.job_status)

  // Fail-closed: never show READY unless the backend file status is ready.
  if (status === 'ready') return FILE_STAGES.ready

  if (job === 'running') {
    const extractionDone = extraction === 'complete' || extraction === 'partial'
    if (index === 'indexing' || (extractionDone && index !== 'indexed')) {
      return FILE_STAGES.indexing
    }
    return FILE_STAGES.extracting
  }
  if (job === 'queued') return FILE_STAGES.queued
  if (job === 'failed') return FILE_STAGES.failed

  // succeeded / cancelled / none: ignore stale extracting/indexing file flags.
  if (status === 'failed' || extraction === 'failed' || index === 'failed') {
    return FILE_STAGES.failed
  }
  return FILE_STAGES.queued
}

export function fileStageLabel(stage, file, upload) {
  const s = normalizeFileStatus(stage)
  if (s === FILE_STAGES.uploading) {
    return formatUploadProgressLabel(upload) || FILE_STAGE_LABELS.uploading
  }
  if (s === FILE_STAGES.extracting) {
    const pages = pageProgress(file)
    if (pages) return `Processing page ${pages.x} of ${pages.y}`
    return FILE_STAGE_LABELS.extracting
  }
  if (FILE_STAGE_LABELS[s]) return FILE_STAGE_LABELS[s]
  return stage || '—'
}

export function formatUploadProgressLabel(upload) {
  if (!upload) return FILE_STAGE_LABELS.uploading
  const loaded = asInt(upload.loaded)
  const total = asInt(upload.total)
  const percent = asInt(upload.percent)
  if (percent != null && total != null && total > 0) {
    return `Uploading ${percent}%`
  }
  if (loaded != null && total != null && total > 0) {
    const pct = Math.min(100, Math.round((loaded / total) * 100))
    return `Uploading ${pct}%`
  }
  return FILE_STAGE_LABELS.uploading
}

export function formatUploadBytes(upload) {
  if (!upload) return ''
  const loaded = asInt(upload.loaded)
  const total = asInt(upload.total)
  if (loaded == null || total == null || total <= 0) return ''
  return `${formatByteSize(loaded)} / ${formatByteSize(total)}`
}

export function formatByteSize(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function fileStatusLabel(status, file) {
  const record =
    file && typeof file === 'object'
      ? file
      : status && typeof status === 'object'
        ? status
        : { status }
  return fileStageLabel(deriveFileStage(record, { upload: record.upload }), record, record.upload)
}

export function isTerminalFileStatus(status) {
  const s = normalizeFileStatus(status)
  return s === 'ready' || s === 'failed'
}

export function isNonTerminalFileStatus(status) {
  const s = normalizeFileStatus(status)
  return s === 'queued' || s === 'processing' || s === 'uploaded' || s === 'extracting' || s === 'indexing'
}

export function isNonTerminalFile(file, upload) {
  if (upload && (upload.phase === 'uploading' || upload.phase === 'failed' && !file?.id)) {
    return upload.phase === 'uploading'
  }
  const stage = deriveFileStage(file, { upload })
  return stage === FILE_STAGES.uploading || stage === FILE_STAGES.queued || stage === FILE_STAGES.extracting || stage === FILE_STAGES.indexing
}

export function processingPercent(file, upload) {
  const stage = deriveFileStage(file, { upload })
  if (stage === FILE_STAGES.uploading) {
    const total = asInt(upload?.total)
    const loaded = asInt(upload?.loaded)
    const percent = asInt(upload?.percent)
    if (percent != null) return percent
    if (loaded != null && total != null && total > 0) {
      return Math.min(100, Math.round((loaded / total) * 100))
    }
    return null
  }
  return null
}

/**
 * Merge server files with in-flight uploads so Sidebar, File Library, and
 * composer attachment all render the same rows.
 */
export function mergeFileInventory(files, uploads) {
  const list = Array.isArray(files) ? files : []
  const ups = Array.isArray(uploads) ? uploads : []
  const byId = new Map()
  for (const file of list) {
    if (!file || typeof file !== 'object') continue
    const id = String(file.id || '').trim()
    if (!id) continue
    byId.set(id, { ...file, upload: null })
  }
  const pending = []
  for (const upload of ups) {
    if (!upload || typeof upload !== 'object') continue
    const fileId = String(upload.fileId || '').trim()
    if (fileId && byId.has(fileId)) {
      byId.get(fileId).upload = upload.phase === 'uploading' ? upload : upload
      continue
    }
    if (fileId) continue
    pending.push({
      id: upload.localId,
      display_name: upload.name,
      original_filename: upload.name,
      byte_size: upload.byteSize ?? upload.total ?? 0,
      status: upload.phase === 'failed' ? 'failed' : 'uploading',
      processing_stage: upload.phase === 'failed' ? 'failed' : 'uploading',
      failure_message: upload.error || null,
      upload,
    })
  }
  return [...pending, ...byId.values()]
}

export function stagesByFileId(rows) {
  const out = {}
  for (const row of rows || []) {
    const id = String(row?.id || '').trim()
    if (!id) continue
    out[id] = deriveFileStage(row, { upload: row.upload })
  }
  return out
}

/**
 * Only filenames the backend reported as actually injected.
 * Never infer from workspace inventory, UI selection, or model text.
 * `workspace_files_used` is the source of truth; do not require
 * `workspace_files_injected === true` (that live-only gate dropped Used files).
 */
export function sanitizeUsedFiles(used) {
  if (!Array.isArray(used) || used.length === 0) return []
  const out = []
  const seen = new Set()
  for (const item of used) {
    if (!item || typeof item !== 'object') continue
    const id = String(item.id || '').trim()
    const name = String(item.name || '').trim()
    if (!id || !name || seen.has(id)) continue
    seen.add(id)
    out.push({ id, name })
  }
  return out
}

export function usedFilesFromDoneEvent(event) {
  if (!event) return []
  return sanitizeUsedFiles(event.workspace_files_used)
}

export function isStandardChatAssistant(message) {
  if (!message || message.role !== 'assistant') return false
  const kind = message.kind
  return !kind || kind === 'chat'
}

export function unavailableChatNote(count) {
  const n = Number(count)
  if (!Number.isFinite(n) || n <= 0) return ''
  if (n === 1) {
    return '1 file in this workspace was not available to this answer (queued or still processing).'
  }
  return `${n} files in this workspace were not available to this answer (queued or still processing).`
}

/**
 * Bounded status poller: one in-flight request, immediate first refresh,
 * then fixed interval. Stop on terminal state or unmount. No WebSockets.
 */
export function createBoundedStatusPoller({
  shouldPoll,
  refresh,
  intervalMs = FILE_STATUS_POLL_MS,
  scheduler = globalThis,
  immediate = true,
} = {}) {
  let timer = null
  let inFlight = false
  let stopped = false

  function clearScheduled() {
    if (timer != null) {
      scheduler.clearTimeout(timer)
      timer = null
    }
  }

  async function tick() {
    timer = null
    if (stopped || inFlight) return
    if (typeof shouldPoll === 'function' && !shouldPoll()) {
      stop()
      return
    }
    inFlight = true
    try {
      if (typeof refresh === 'function') {
        await refresh()
      }
    } finally {
      inFlight = false
      if (stopped) return
      if (typeof shouldPoll === 'function' && !shouldPoll()) {
        stop()
        return
      }
      timer = scheduler.setTimeout(tick, intervalMs)
    }
  }

  function start() {
    if (stopped) return
    clearScheduled()
    if (typeof shouldPoll === 'function' && !shouldPoll()) return
    if (immediate) {
      void tick()
    } else {
      timer = scheduler.setTimeout(tick, intervalMs)
    }
  }

  function stop() {
    stopped = true
    clearScheduled()
  }

  return {
    start,
    stop,
    get stopped() {
      return stopped
    },
    get inFlight() {
      return inFlight
    },
  }
}
