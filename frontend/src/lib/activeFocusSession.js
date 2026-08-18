/**
 * Active Context Focus loader with sticky-error recovery.
 * HTTP 401 still surfaces. A later authenticated 200 clears it.
 * Token-null does not send and retries until a Bearer can be acquired.
 */
import { isAuthTokenUnavailable } from '../api/benHeaders.js'

function emptySnapshot() {
  return {
    data: null,
    error: null,
    loading: false,
  }
}

function headerIdentity(headers = {}) {
  return String(headers.Authorization || headers.authorization || '').trim()
}

export function createActiveFocusController({
  acquireHeaders,
  fetchFocus,
  retryDelayMs = 200,
} = {}) {
  let snapshot = emptySnapshot()
  const listeners = new Set()
  let generation = 0
  let timer = null
  let lastIdentity = ''

  function emit() {
    snapshot = { ...snapshot }
    listeners.forEach((fn) => {
      try {
        fn()
      } catch {
        /* ignore */
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

  function clearTimer() {
    if (timer == null) return
    clearTimeout(timer)
    timer = null
  }

  function stop() {
    generation += 1
    clearTimer()
  }

  function schedule(gen, fn) {
    clearTimer()
    timer = setTimeout(() => {
      timer = null
      if (gen !== generation) return
      fn()
    }, retryDelayMs)
  }

  async function run(request, gen) {
    if (gen !== generation) return
    snapshot = { ...snapshot, loading: true }
    emit()
    try {
      const headers = await acquireHeaders()
      if (gen !== generation) return
      lastIdentity = headerIdentity(headers)
      const data = await fetchFocus(request.projectSlug, request.threadId, request.query, headers)
      if (gen !== generation) return
      snapshot = { data, error: null, loading: false }
      emit()
    } catch (e) {
      if (gen !== generation) return
      if (isAuthTokenUnavailable(e)) {
        snapshot = { ...snapshot, loading: false }
        emit()
        schedule(gen, () => {
          void run(request, gen)
        })
        return
      }
      snapshot = {
        data: null,
        error: e?.message || 'Could not load active context focus',
        loading: false,
      }
      emit()
      const failedIdentity = lastIdentity
      if (e?.status === 401) {
        schedule(gen, () => {
          void recoverAfter401(request, gen, failedIdentity, 0)
        })
      }
    }
  }

  async function recoverAfter401(request, gen, failedIdentity, attempt) {
    if (gen !== generation) return
    if (attempt > 12) return
    try {
      const headers = await acquireHeaders()
      if (gen !== generation) return
      const next = headerIdentity(headers)
      if (!next || next === failedIdentity) {
        schedule(gen, () => {
          void recoverAfter401(request, gen, failedIdentity, attempt + 1)
        })
        return
      }
      void run(request, gen)
    } catch (e) {
      if (gen !== generation) return
      if (isAuthTokenUnavailable(e)) {
        schedule(gen, () => {
          void recoverAfter401(request, gen, failedIdentity, attempt + 1)
        })
      }
    }
  }

  function start(request) {
    generation += 1
    clearTimer()
    lastIdentity = ''
    const gen = generation
    void run(request, gen)
  }

  return {
    start,
    stop,
    subscribe,
    getSnapshot,
  }
}
