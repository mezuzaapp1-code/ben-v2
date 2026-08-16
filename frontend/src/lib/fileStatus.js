/** Gate 1 — honest file status + used-files helpers. Backend status is source of truth. */

export const FILE_STATUS_POLL_MS = 8000

export const FILE_STATUS_LABELS = {
  queued: 'Queued — not available to chat yet',
  uploaded: 'Queued — not available to chat yet',
  processing: 'Processing — not available to chat yet',
  ready: 'Ready — available to chat',
  failed: 'Failed',
}

export function normalizeFileStatus(status) {
  return String(status || '')
    .trim()
    .toLowerCase()
}

export function fileStatusLabel(status) {
  const s = normalizeFileStatus(status)
  if (s === 'uploaded') return FILE_STATUS_LABELS.queued
  if (FILE_STATUS_LABELS[s]) return FILE_STATUS_LABELS[s]
  return status || '—'
}

export function isTerminalFileStatus(status) {
  const s = normalizeFileStatus(status)
  return s === 'ready' || s === 'failed'
}

export function isNonTerminalFileStatus(status) {
  const s = normalizeFileStatus(status)
  return s === 'queued' || s === 'processing' || s === 'uploaded'
}

/**
 * Only filenames the backend reported as actually injected.
 * Never infer from workspace inventory, UI selection, or model text.
 */
export function usedFilesFromDoneEvent(event) {
  if (!event || event.workspace_files_injected !== true) return []
  const used = event.workspace_files_used
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

export function unavailableChatNote(count) {
  const n = Number(count)
  if (!Number.isFinite(n) || n <= 0) return ''
  if (n === 1) {
    return '1 file in this workspace was not available to this answer (queued or still processing).'
  }
  return `${n} files in this workspace were not available to this answer (queued or still processing).`
}

/**
 * Bounded status poller: one in-flight request, fixed interval, stop on
 * terminal state or unmount. No WebSockets.
 */
export function createBoundedStatusPoller({
  shouldPoll,
  refresh,
  intervalMs = FILE_STATUS_POLL_MS,
  scheduler = globalThis,
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
    timer = scheduler.setTimeout(tick, intervalMs)
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
  }
}
