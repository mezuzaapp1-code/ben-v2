import { OrganizationSwitcher, SignInButton, SignOutButton, useAuth } from '@clerk/clerk-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildBenHeaders } from './api/benHeaders.js'
import {
  CLERK_ORG_REQUIRED,
  humanizeBenHttpError,
  parseBenErrorResponse,
  readJsonResponse,
} from './api/benErrors.js'
import {
  COUNCIL_CLIENT_TIMEOUT_MS,
  councilResponseToMessages,
  humanizeCouncilFetchError,
  humanizeCouncilHttpError,
  postCouncil,
} from './api/council.js'
import { CouncilProgressPanel } from './CouncilProgressPanel.jsx'
import {
  COUNCIL_PHASE_SCHEDULE_MS,
  isLongCouncilPrompt,
  isRtlPreferredText,
  sanitizeCouncilErrorMessage,
} from './councilProgress.js'
import { fetchThreadDetail, fetchThreadList, mapApiMessage, mapThreadFromList } from './api/threads.js'
import { logCouncilLifecycle } from './councilLifecycleLog.js'
import { useBenAuthContext } from './auth/BenAuthContext.jsx'
import { BEN_API_BASE } from './config.js'
import {
  DRAFT_PREFIX,
  getStoredActiveThreadId,
  isPersistedThreadId,
  serverThreadIdForApi,
  setStoredActiveThreadId,
} from './threadStorage.js'
import './App.css'

const CHAT_URL = `${BEN_API_BASE}/chat`
const HAS_CLERK_UI = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())

const COUNCIL_LABEL = {
  'Legal Advisor': '⚖️ Legal Advisor',
  'Business Advisor': '💼 Business Advisor',
  'Strategy Advisor': '🎯 Strategy Advisor',
}

function bubbleTextProps(content) {
  const rtl = isRtlPreferredText(content)
  return {
    className: `bubble-text${rtl ? ' bubble-text--rtl' : ''}`,
    dir: rtl ? 'rtl' : 'auto',
  }
}

function expertStatusLabel(outcome, response) {
  if (!outcome || outcome === 'ok') return null
  if (outcome === 'timeout') return 'Unavailable: timeout'
  const m = /Expert unavailable \(([^)]+)\)/.exec(response || '')
  if (outcome === 'degraded' && m) return `Degraded: ${m[1]}`
  if (outcome === 'error') return 'Degraded: error'
  return `Degraded: ${outcome}`
}

const SYNTHESIS_REASONING_SECTIONS = [
  ['shared_recommendation', 'Shared recommendation'],
  ['disagreement_points', 'Disagreement & rationale'],
  ['legal_reasoning', 'Legal reasoning'],
  ['operational_reasoning', 'Operational reasoning'],
  ['strategic_reasoning', 'Strategic reasoning'],
  ['infrastructure_reasoning', 'Infrastructure reasoning'],
  ['minority_or_unique_views', 'Minority or unique views'],
]

function SynthesisReasoningExtras({ synthesis }) {
  const blocks = SYNTHESIS_REASONING_SECTIONS.map(([key, label]) => {
    const v = synthesis[key]
    if (v == null || String(v).trim() === '') return null
    return (
      <details key={key} className="synthesis-detail">
        <summary>{label}</summary>
        <div className="synthesis-detail-body">{String(v)}</div>
      </details>
    )
  }).filter(Boolean)
  if (blocks.length === 0) return null
  return <div className="synthesis-reasoning-extras">{blocks}</div>
}

function councilSynthesisBubbleText(s, anyExpertFailed) {
  const disagree =
    s.main_disagreement != null && String(s.main_disagreement).trim() !== ''
      ? String(s.main_disagreement)
      : 'None'
  const ae = s.agreement_estimate ?? 'unknown'
  const rec = s.recommendation ?? ''
  const cons = s.consensus_points ?? ''
  const prefix = anyExpertFailed ? 'Based on available expert responses.\n\n' : ''
  return `${prefix}🧠 BEN Synthesis (${ae})
${rec}

✅ Consensus: ${cons}
⚡ Disagreement: ${disagree}

This is a structured reasoning layer, not a final answer.`
}

function OrgRecoveryBanner({ banner, onDismiss }) {
  if (!banner) return null
  return (
    <div className="org-recovery-banner" role="alert">
      <p className="org-recovery-title">{banner.message}</p>
      {banner.hint ? <p className="org-recovery-hint">{banner.hint}</p> : null}
      {onDismiss ? (
        <button type="button" className="org-recovery-dismiss" onClick={onDismiss}>
          Dismiss
        </button>
      ) : null}
    </div>
  )
}

function ClerkAuthControls() {
  const { isSignedIn } = useAuth()
  if (!HAS_CLERK_UI) return null
  return (
    <div className="auth-controls">
      {isSignedIn ? (
        <>
          <OrganizationSwitcher hidePersonal />
          <SignOutButton>
            <button type="button" className="auth-btn">
              Sign out
            </button>
          </SignOutButton>
        </>
      ) : (
        <SignInButton mode="modal">
          <button type="button" className="auth-btn">
            Sign in
          </button>
        </SignInButton>
      )}
    </div>
  )
}

function App() {
  const { getToken } = useBenAuthContext()
  const [threads, setThreads] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [input, setInput] = useState('')
  const [tier, setTier] = useState('free')
  const [loading, setLoading] = useState(false)
  const [hydrating, setHydrating] = useState(true)
  const [orgBanner, setOrgBanner] = useState(null)
  const [councilProgress, setCouncilProgress] = useState(null)
  const councilPhaseTimersRef = useRef([])

  const clearCouncilPhaseTimers = useCallback(() => {
    councilPhaseTimersRef.current.forEach((id) => clearTimeout(id))
    councilPhaseTimersRef.current = []
  }, [])

  const startCouncilPhaseTimers = useCallback((longPrompt) => {
    clearCouncilPhaseTimers()
    setCouncilProgress({ activePhase: 'started', longPrompt })
    logCouncilLifecycle('council_phase', { phase: 'started' })
    COUNCIL_PHASE_SCHEDULE_MS.forEach(({ at, phase }) => {
      if (phase === 'started') return
      const id = setTimeout(() => {
        setCouncilProgress((prev) => (prev ? { ...prev, activePhase: phase } : null))
        logCouncilLifecycle('council_phase', { phase })
      }, at)
      councilPhaseTimersRef.current.push(id)
    })
  }, [clearCouncilPhaseTimers])

  const inputLongPrompt = useMemo(() => isLongCouncilPrompt(input), [input])

  const active = useMemo(
    () => threads.find((t) => t.id === activeId) ?? null,
    [threads, activeId]
  )

  const loadThreadMessages = useCallback(
    async (threadId) => {
      if (!isPersistedThreadId(threadId)) return
      const headers = await buildBenHeaders(getToken)
      const data = await fetchThreadDetail(threadId, headers)
      const messages = (data.messages || []).map(mapApiMessage)
      setThreads((prev) =>
        prev.map((t) =>
          t.id === threadId
            ? { ...t, title: data.thread?.title || t.title, messages, loaded: true }
            : t
        )
      )
    },
    [getToken]
  )

  const selectThread = useCallback(
    async (threadId) => {
      setActiveId(threadId)
      if (isPersistedThreadId(threadId)) setStoredActiveThreadId(threadId)
      const t = threads.find((x) => x.id === threadId)
      if (t && isPersistedThreadId(threadId) && !t.loaded) {
        try {
          await loadThreadMessages(threadId)
        } catch {
          /* keep partial UI */
        }
      }
    },
    [threads, loadThreadMessages]
  )

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setHydrating(true)
      try {
        const headers = await buildBenHeaders(getToken)
        const data = await fetchThreadList(headers)
        if (cancelled) return
        const serverThreads = (data.threads || []).map(mapThreadFromList)
        const stored = getStoredActiveThreadId()
        const active =
          stored && serverThreads.some((t) => t.id === stored)
            ? stored
            : serverThreads[0]?.id ?? null
        setThreads(serverThreads)
        setActiveId(active)
        if (active && isPersistedThreadId(active)) {
          await loadThreadMessages(active)
        }
      } catch (e) {
        if (!cancelled) {
          if (e.parsed?.code === CLERK_ORG_REQUIRED) {
            setOrgBanner({ message: e.parsed.message, hint: e.parsed.hint })
            const stored = getStoredActiveThreadId()
            if (stored) {
              setActiveId(stored)
              setThreads((prev) => {
                if (prev.some((t) => t.id === stored)) return prev
                return [{ id: stored, title: 'Conversation', messages: [], loaded: false }, ...prev]
              })
            }
          } else {
            const stored = getStoredActiveThreadId()
            if (stored && isPersistedThreadId(stored)) {
              setActiveId(stored)
              setThreads((prev) => {
                if (prev.some((t) => t.id === stored)) return prev
                return [{ id: stored, title: 'Conversation', messages: [], loaded: false }, ...prev]
              })
              try {
                await loadThreadMessages(stored)
              } catch (inner) {
                if (inner.parsed?.code === CLERK_ORG_REQUIRED) {
                  setOrgBanner({ message: inner.parsed.message, hint: inner.parsed.hint })
                }
              }
            }
          }
        }
      } finally {
        if (!cancelled) setHydrating(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [getToken, loadThreadMessages])

  const newThread = useCallback(() => {
    const id = `${DRAFT_PREFIX}${crypto.randomUUID()}`
    const t = { id, title: 'New conversation', messages: [], loaded: true, isDraft: true }
    setThreads((prev) => [t, ...prev])
    setActiveId(id)
    setStoredActiveThreadId(null)
    return id
  }, [])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return
    let tid = activeId
    if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
    const userMsg = { role: 'user', content: text }
    setInput('')
    setThreads((prev) =>
      prev.map((t) =>
        t.id === tid ? { ...t, title: text.slice(0, 48) || t.title, messages: [...t.messages, userMsg] } : t
      )
    )
    setLoading(true)
    try {
      const headers = await buildBenHeaders(getToken)
      const apiThreadId = serverThreadIdForApi(tid)
      const res = await fetch(CHAT_URL, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message: text,
          tier,
          ...(apiThreadId ? { thread_id: apiThreadId } : {}),
        }),
      })
      const data = await readJsonResponse(res)
      if (!res.ok) {
        const parsed = parseBenErrorResponse(res.status, data)
        if (parsed?.code === CLERK_ORG_REQUIRED) {
          setOrgBanner({ message: parsed.message, hint: parsed.hint })
          return
        }
        setThreads((prev) =>
          prev.map((t) =>
            t.id === tid
              ? {
                  ...t,
                  messages: [
                    ...t.messages,
                    {
                      role: 'assistant',
                      kind: 'api_error',
                      content: humanizeBenHttpError(res.status, data),
                      model_used: '',
                      cost_usd: 0,
                    },
                  ],
                }
              : t
          )
        )
        return
      }
      setOrgBanner(null)
      const assistant = {
        role: 'assistant',
        content: data.response ?? '',
        model_used: data.model_used ?? '',
        cost_usd: data.cost_usd ?? 0,
      }
      const serverTid = data.thread_id
      setThreads((prev) => {
        const nextList = prev.map((t) => {
          if (t.id !== tid) return t
          const next = { ...t, messages: [...t.messages, assistant], loaded: true, isDraft: false }
          if (serverTid && serverTid !== tid) {
            next.id = serverTid
          }
          return next
        })
        if (serverTid && !nextList.some((t) => t.id === serverTid)) {
          const src = nextList.find((t) => t.id === tid)
          if (src) nextList.unshift({ ...src, id: serverTid })
        }
        return nextList
      })
      if (serverTid) {
        setActiveId(serverTid)
        setStoredActiveThreadId(serverTid)
      }
    } catch (e) {
      const msg = e?.message || 'Chat failed. You can retry.'
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  { role: 'assistant', kind: 'api_error', content: msg, model_used: '', cost_usd: 0 },
                ],
              }
            : t
        )
      )
    } finally {
      setLoading(false)
    }
  }, [input, loading, activeId, threads, tier, newThread, getToken])

  const applyCouncilMessages = useCallback((tid, extras, resolvedId) => {
    setThreads((prev) => {
      let next = prev.map((t) =>
        t.id === tid
          ? {
              ...t,
              messages: [...t.messages, ...extras],
              loaded: true,
              isDraft: false,
              ...(resolvedId ? { id: resolvedId } : {}),
            }
          : t
      )
      if (resolvedId && !next.some((t) => t.id === resolvedId)) {
        const src = next.find((t) => t.id === tid || t.id === resolvedId)
        if (src) next = [{ ...src, id: resolvedId }, ...next.filter((t) => t.id !== tid)]
      }
      return next
    })
    if (resolvedId) {
      setActiveId(resolvedId)
      setStoredActiveThreadId(resolvedId)
    }
  }, [])

  const council = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return
    let tid = activeId
    if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
    const apiThreadId = serverThreadIdForApi(tid)
    const userMsg = { role: 'user', content: text }
    setInput('')
    setThreads((prev) =>
      prev.map((t) =>
        t.id === tid ? { ...t, title: text.slice(0, 48) || t.title, messages: [...t.messages, userMsg] } : t
      )
    )

    const longPrompt = isLongCouncilPrompt(text)
    logCouncilLifecycle('council_submit_started', {
      hasThreadId: Boolean(apiThreadId),
      longPrompt,
    })
    startCouncilPhaseTimers(longPrompt)

    const controller = new AbortController()
    const abortTimer = setTimeout(() => controller.abort(), COUNCIL_CLIENT_TIMEOUT_MS)
    setLoading(true)

    try {
      const headers = await buildBenHeaders(getToken)
      logCouncilLifecycle('council_request_sent', {
        hasAuth: Boolean(headers.Authorization),
        hasThreadId: Boolean(apiThreadId),
      })

      const { res, data } = await postCouncil({
        question: text,
        threadId: apiThreadId,
        headers,
        signal: controller.signal,
      })
      logCouncilLifecycle('council_response_received', { status: res.status })

      if (!res.ok) {
        const parsed = parseBenErrorResponse(res.status, data)
        if (parsed?.code === CLERK_ORG_REQUIRED) {
          setOrgBanner({ message: parsed.message, hint: parsed.hint })
          logCouncilLifecycle('council_submit_failed', {
            status: res.status,
            reason: 'clerk_org_required',
          })
          return
        }
        const errText = humanizeCouncilHttpError(res.status, data)
        logCouncilLifecycle('council_submit_failed', { status: res.status, reason: 'http_error' })
        setThreads((prev) =>
          prev.map((t) =>
            t.id === tid
              ? {
                  ...t,
                  messages: [
                    ...t.messages,
                    { role: 'assistant', kind: 'council_error', content: errText, model_used: '', cost_usd: 0 },
                  ],
                }
              : t
          )
        )
        return
      }

      setOrgBanner(null)
      const extras = councilResponseToMessages(data, councilSynthesisBubbleText)
      const anyDegraded = extras.some((m) => m.expert_degraded)
      applyCouncilMessages(tid, extras, apiThreadId)
      logCouncilLifecycle('council_render_completed', {
        messageCount: extras.length,
        anyDegraded,
      })

      if (!apiThreadId) {
        void (async () => {
          try {
            const listData = await fetchThreadList(headers)
            const latest = listData.threads?.[0]
            if (latest?.id) {
              setThreads((prev) =>
                prev.map((t) =>
                  t.id === tid ? { ...t, id: latest.id, isDraft: false, loaded: true } : t
                )
              )
              setActiveId(latest.id)
              setStoredActiveThreadId(latest.id)
            }
          } catch (inner) {
            if (inner.parsed?.code === CLERK_ORG_REQUIRED) {
              setOrgBanner({ message: inner.parsed.message, hint: inner.parsed.hint })
            }
          }
        })()
      }
    } catch (e) {
      const errText = sanitizeCouncilErrorMessage(humanizeCouncilFetchError(e))
      logCouncilLifecycle('council_submit_failed', {
        reason: e?.name || 'error',
      })
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  { role: 'assistant', kind: 'council_error', content: errText, model_used: '', cost_usd: 0 },
                ],
              }
            : t
        )
      )
    } finally {
      clearTimeout(abortTimer)
      clearCouncilPhaseTimers()
      setCouncilProgress(null)
      setLoading(false)
      logCouncilLifecycle('council_submit_finally')
    }
  }, [
    input,
    loading,
    activeId,
    threads,
    newThread,
    getToken,
    startCouncilPhaseTimers,
    clearCouncilPhaseTimers,
    applyCouncilMessages,
  ])

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">BEN</div>
        {HAS_CLERK_UI ? <ClerkAuthControls /> : null}
        <button type="button" className="new-btn" onClick={newThread}>
          + New chat
        </button>
        <ul className="thread-list">
          {threads.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                className={t.id === activeId ? 'thread active' : 'thread'}
                onClick={() => selectThread(t.id)}
              >
                {t.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="main">
        <OrgRecoveryBanner banner={orgBanner} onDismiss={() => setOrgBanner(null)} />
        <div className="messages">
          {hydrating && threads.length === 0 ? (
            <div className="hydrate-hint">Loading conversations…</div>
          ) : null}
          <CouncilProgressPanel
            activePhase={councilProgress?.activePhase}
            longPrompt={councilProgress?.longPrompt}
          />
          {(active?.messages ?? []).map((m, i) => (
            <div
              key={i}
              className={`bubble-wrap ${m.role}${m.kind === 'council_synthesis' ? ' synthesis-wrap' : ''}`}
            >
              {m.kind === 'council_synthesis' && m.synthesis ? (
                <div className="bubble synthesis">
                  <div {...bubbleTextProps(m.content)}>{m.content}</div>
                  <SynthesisReasoningExtras synthesis={m.synthesis} />
                  {(m.model_used || m.cost_usd !== undefined) && (
                    <div className="meta">
                      {m.model_used && <span>{m.model_used}</span>}
                      {m.model_used && <span className="dot">·</span>}
                      <span>${Number(m.cost_usd).toFixed(6)}</span>
                    </div>
                  )}
                </div>
              ) : (
                <div
                  className={`bubble ${m.role}${m.kind === 'council_error' ? ' council-error' : ''}${m.kind === 'api_error' ? ' api-error' : ''}${m.expert_degraded ? ' expert-degraded' : ''}`}
                >
                  <div {...bubbleTextProps(m.content)}>{m.content}</div>
                  {m.role === 'assistant' &&
                    m.kind !== 'council_error' &&
                    m.kind !== 'api_error' &&
                    (m.model_used || m.cost_usd !== undefined || m.expert_status) && (
                    <div className="meta">
                      {m.expert_status && <span className="expert-status">{m.expert_status}</span>}
                      {m.expert_status && m.model_used && <span className="dot">·</span>}
                      {m.model_used && <span>{m.model_used}</span>}
                      {m.model_used && <span className="dot">·</span>}
                      <span>${Number(m.cost_usd).toFixed(6)}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
        <footer className="composer">
          {inputLongPrompt && !loading ? (
            <p className="composer-long-hint" role="status">
              This is a complex request and may take longer.
            </p>
          ) : null}
          <select className="tier" value={tier} onChange={(e) => setTier(e.target.value)} aria-label="Tier">
            <option value="free">free</option>
            <option value="pro">pro</option>
          </select>
          <input
            className="input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), send())}
            placeholder="Message BEN…"
            disabled={loading}
            dir={isRtlPreferredText(input) ? 'rtl' : 'auto'}
          />
          <button type="button" className="council" onClick={council} disabled={loading || !input.trim()}>
            Council
          </button>
          <button type="button" className="send" onClick={send} disabled={loading || !input.trim()}>
            {loading ? '…' : 'Send'}
          </button>
        </footer>
      </main>
    </div>
  )
}

export default App
