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
  postCouncilStream,
} from './api/council.js'
import { fetchThreadDetail, fetchThreadList, mapApiMessage, mapThreadFromList } from './api/threads.js'
import { logCouncilLifecycle } from './councilLifecycleLog.js'
import {
  canSubmitCouncil,
  markCouncilSubmitFinished,
  markCouncilSubmitStarted,
} from './loadGovernance.js'
import {
  clearCouncilPending,
  createClientRequestId,
  markCouncilPending,
  recoverStaleCouncilUi,
} from './runtimeRecovery.js'
import { useBenAuthContext } from './auth/BenAuthContext.jsx'
import { BEN_API_BASE } from './config.js'
import {
  DRAFT_PREFIX,
  getStoredActiveThreadId,
  isDraftThreadId,
  isPersistedThreadId,
  serverThreadIdForApi,
  setStoredActiveThreadId,
} from './threadStorage.js'
import { ProviderToolbar } from './providers/ProviderToolbar.jsx'
import { formatChatAssistantMeta } from './providers/formatChatMeta.js'
import { DEFAULT_SPEAKING_PROVIDER_ID, getSpeakingProviderById } from './providers/providerRegistry.js'
import './App.css'

const CHAT_URL = `${BEN_API_BASE}/chat`
const HAS_CLERK_UI = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())
const USE_COUNCIL_STREAM = true

const COUNCIL_PHASE_TIMERS = [
  { at: 0, phase: 'started', message: 'Council started…' },
  { at: 300, phase: 'experts', message: 'Waiting for Legal, Business, and Strategy…' },
  { at: 12_000, phase: 'synthesizing', message: 'Synthesizing…' },
]

const COUNCIL_LABEL = {
  'Legal Advisor': '⚖️ Legal Advisor',
  'Business Advisor': '💼 Business Advisor',
  'Strategy Advisor': '🎯 Strategy Advisor',
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

function CopyIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  )
}

function EditIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  )
}

const messageActionBtnStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.35rem',
  padding: '0.2rem 0.35rem',
  fontSize: '0.75rem',
  lineHeight: 1.2,
  background: 'transparent',
  border: 'none',
  borderRadius: '4px',
  color: '#888',
  cursor: 'pointer',
}

function MessageActionBar({ role, content, onEditRequest }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(String(content ?? ''))
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div
      className="message-action-bar"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.65rem',
        marginTop: '0.3rem',
        padding: '0 0.1rem',
      }}
    >
      {role === 'user' && onEditRequest ? (
        <button
          type="button"
          style={messageActionBtnStyle}
          onClick={() => onEditRequest(String(content ?? ''))}
          aria-label="Edit request"
        >
          <EditIcon />
          <span>עריכת בקשה</span>
        </button>
      ) : null}
      <button
        type="button"
        style={{
          ...messageActionBtnStyle,
          color: copied ? '#6fcf97' : '#888',
        }}
        onClick={handleCopy}
        aria-label={copied ? 'Copied' : 'Copy message'}
      >
        {copied ? (
          <>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6fcf97" strokeWidth="2.5">
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <span>הועתק</span>
          </>
        ) : (
          <>
            <CopyIcon />
            <span>העתקה</span>
          </>
        )}
      </button>
    </div>
  )
}

function conversationCopyLabel(m) {
  if (m.role === 'user') return 'User'
  if (m.kind === 'council_synthesis') return 'BEN Synthesis'
  const providerLabel = getSpeakingProviderById(m.provider_id)?.label
  if (providerLabel) return providerLabel
  const content = String(m.content ?? '')
  for (const [, head] of Object.entries(COUNCIL_LABEL)) {
    if (content.startsWith(`${head}: `)) return head
  }
  return 'Assistant'
}

function conversationCopyBody(m) {
  const content = String(m.content ?? '')
  if (m.role === 'user' || m.kind === 'council_synthesis') return content
  for (const head of Object.values(COUNCIL_LABEL)) {
    const prefix = `${head}: `
    if (content.startsWith(prefix)) return content.slice(prefix.length)
  }
  return content
}

function formatConversationForCopy(messages) {
  return (messages ?? [])
    .map((m) => {
      const body = conversationCopyBody(m).trim()
      if (!body) return null
      return `${conversationCopyLabel(m)}: ${body}`
    })
    .filter(Boolean)
    .join('\n\n')
}

function CopyConversationButton({ messages }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(formatConversationForCopy(messages))
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <button
      type="button"
      className="copy-conversation-btn"
      onClick={handleCopy}
      aria-label={copied ? 'Conversation copied' : 'Copy conversation'}
      style={{
        alignSelf: 'flex-start',
        margin: '0 0 0.5rem',
        padding: '0.35rem 0.65rem',
        fontSize: '0.8rem',
        background: 'transparent',
        border: '1px solid #333',
        borderRadius: '6px',
        color: copied ? '#6fcf97' : '#aaa',
        cursor: 'pointer',
      }}
    >
      {copied ? 'Copied ✓' : 'Copy conversation'}
    </button>
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
  const [councilStatus, setCouncilStatus] = useState(null)
  const [activeSpeakingProviderId, setActiveSpeakingProviderId] = useState(
    DEFAULT_SPEAKING_PROVIDER_ID
  )
  const councilPhaseTimersRef = useRef([])

  const clearCouncilPhaseTimers = useCallback(() => {
    councilPhaseTimersRef.current.forEach((id) => clearTimeout(id))
    councilPhaseTimersRef.current = []
  }, [])

  const startCouncilPhaseTimers = useCallback(() => {
    clearCouncilPhaseTimers()
    COUNCIL_PHASE_TIMERS.forEach(({ at, phase, message }) => {
      const id = setTimeout(() => {
        setCouncilStatus({ phase, message })
        logCouncilLifecycle('council_phase', { phase })
      }, at)
      councilPhaseTimersRef.current.push(id)
    })
  }, [clearCouncilPhaseTimers])

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
    if (recoverStaleCouncilUi()) {
      setLoading(false)
      setCouncilStatus(null)
      logCouncilLifecycle('stale_runtime_state_recovered')
    }
  }, [])

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

  const handleEditRequest = useCallback((text) => {
    setInput(text)
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
      const clientRequestId = createClientRequestId()
      const res = await fetch(CHAT_URL, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message: text,
          tier,
          provider_id: activeSpeakingProviderId,
          client_request_id: clientRequestId,
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
        provider_id: data.provider_id ?? activeSpeakingProviderId,
        provider_used: data.provider_used ?? '',
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
  }, [input, loading, activeId, threads, tier, activeSpeakingProviderId, newThread, getToken])

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
    if (isDraftThreadId(tid)) {
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  {
                    role: 'assistant',
                    kind: 'council_error',
                    content:
                      'Council requires a saved server thread. Send a chat message first.',
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
    const apiThreadId = serverThreadIdForApi(tid)
    const guard = canSubmitCouncil(text, apiThreadId)
    if (!guard.ok) {
      const blocked =
        guard.reason === 'duplicate'
          ? 'This council request was just submitted. Please wait a moment.'
          : 'Council is already in progress. Please wait for it to finish.'
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  { role: 'assistant', kind: 'council_error', content: blocked, model_used: '', cost_usd: 0 },
                ],
              }
            : t
        )
      )
      return
    }
    markCouncilSubmitStarted(guard.fingerprint)
    const clientRequestId = createClientRequestId()
    markCouncilPending({ clientRequestId, threadId: apiThreadId || tid })
    const userMsg = { role: 'user', content: text }
    setInput('')
    setThreads((prev) =>
      prev.map((t) =>
        t.id === tid ? { ...t, title: text.slice(0, 48) || t.title, messages: [...t.messages, userMsg] } : t
      )
    )

    logCouncilLifecycle('council_submit_started', {
      hasThreadId: Boolean(apiThreadId),
    })
    setCouncilStatus({ phase: 'started', message: 'Council started…' })
    startCouncilPhaseTimers()

    const controller = new AbortController()
    setLoading(true)

    try {
      const headers = await buildBenHeaders(getToken)
      logCouncilLifecycle('council_request_sent', {
        hasAuth: Boolean(headers.Authorization),
        hasThreadId: Boolean(apiThreadId),
      })

      const appendCouncilMessage = (msg) => {
        setThreads((prev) =>
          prev.map((t) =>
            t.id === tid
              ? { ...t, messages: [...t.messages, msg], loaded: true, isDraft: false }
              : t
          )
        )
      }

      if (USE_COUNCIL_STREAM) {
        let streamOk = false
        let anyExpertFailed = false
        try {
          for await (const event of postCouncilStream({
            question: text,
            threadId: apiThreadId,
            clientRequestId,
            headers,
            signal: controller.signal,
          })) {
            if (event.type === 'expert') {
              const head = COUNCIL_LABEL[event.role] || event.role || 'Advisor'
              appendCouncilMessage({
                role: 'assistant',
                content: `${head}: ${event.content ?? ''}`,
                model_used: event.model ?? '',
                expert_outcome: 'ok',
                expert_status: null,
                cost_usd: 0,
              })
            } else if (event.type === 'synthesis') {
              const syn = { next_steps: event.next_steps ?? [] }
              appendCouncilMessage({
                role: 'assistant',
                kind: 'council_synthesis',
                synthesis: syn,
                content: event.content ?? councilSynthesisBubbleText(syn, anyExpertFailed),
                model_used: 'synthesis',
                cost_usd: 0,
              })
              streamOk = true
            } else if (event.type === 'error') {
              anyExpertFailed = true
              appendCouncilMessage({
                role: 'assistant',
                kind: 'council_error',
                content: event.message || 'Council failed. You can retry.',
                model_used: '',
                cost_usd: 0,
              })
            }
          }
          if (streamOk) {
            setOrgBanner(null)
            clearCouncilPending()
            logCouncilLifecycle('council_stream_completed')
            return
          }
        } catch (streamErr) {
          const parsed = parseBenErrorResponse(streamErr.status, streamErr.data)
          if (parsed?.code === CLERK_ORG_REQUIRED) {
            setOrgBanner({ message: parsed.message, hint: parsed.hint })
            logCouncilLifecycle('council_submit_failed', {
              status: streamErr.status,
              reason: 'clerk_org_required',
            })
            return
          }
          logCouncilLifecycle('council_stream_fallback', {
            reason: streamErr?.name || 'error',
          })
        }
      }

      const abortTimer = setTimeout(() => controller.abort(), COUNCIL_CLIENT_TIMEOUT_MS)
      try {
        const { res, data } = await postCouncil({
          question: text,
          threadId: apiThreadId,
          clientRequestId,
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
          logCouncilLifecycle('council_submit_failed', {
            status: res.status,
            reason: parsed?.code || 'http_error',
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
          return
        }

        setOrgBanner(null)
        clearCouncilPending()
        const extras = councilResponseToMessages(data, councilSynthesisBubbleText)
        applyCouncilMessages(tid, extras, apiThreadId)
        logCouncilLifecycle('council_render_completed', {
          messageCount: extras.length,
          runtimeState: data.runtime_state,
          idempotentReplay: data.idempotent_replay,
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
      } finally {
        clearTimeout(abortTimer)
      }
    } catch (e) {
      const errText = humanizeCouncilFetchError(e)
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
      clearCouncilPhaseTimers()
      setCouncilStatus(null)
      setLoading(false)
      markCouncilSubmitFinished()
      clearCouncilPending()
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
          {councilStatus ? (
            <div className="council-progress" role="status" aria-live="polite">
              {councilStatus.message}
            </div>
          ) : null}
          {(active?.messages ?? []).map((m, i) => {
            const chatMeta =
              m.role === 'assistant' &&
              m.kind !== 'council_error' &&
              m.kind !== 'api_error' &&
              m.kind !== 'council_synthesis'
                ? formatChatAssistantMeta(m)
                : ''
            return (
            <div
              key={i}
              className={`bubble-wrap ${m.role}${m.kind === 'council_synthesis' ? ' synthesis-wrap' : ''}`}
            >
              <div
                className="bubble-stack"
                style={{
                  maxWidth: 'min(72ch, 92%)',
                  width: 'fit-content',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: m.role === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
              {m.kind === 'council_synthesis' && m.synthesis ? (
                <div className="bubble synthesis">
                  <div className="bubble-text">{m.content}</div>
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
                  className={`bubble ${m.role}${m.kind === 'council_error' ? ' council-error' : ''}${m.kind === 'api_error' ? ' api-error' : ''}`}
                >
                  <div className="bubble-text">{m.content}</div>
                  {m.role === 'assistant' &&
                    m.kind !== 'council_error' &&
                    m.kind !== 'api_error' &&
                    m.kind !== 'council_synthesis' &&
                    (chatMeta || m.model_used || m.cost_usd !== undefined || m.expert_status) && (
                    <div className="meta">
                      {m.expert_status && <span className="expert-status">{m.expert_status}</span>}
                      {m.expert_status && (chatMeta || m.model_used) && <span className="dot">·</span>}
                      <span>{chatMeta || m.model_used || 'Assistant'}</span>
                      {(chatMeta || m.model_used) && <span className="dot">·</span>}
                      <span>${Number(m.cost_usd).toFixed(6)}</span>
                    </div>
                  )}
                </div>
              )}
              {m.role === 'user' || m.role === 'assistant' ? (
                <MessageActionBar
                  role={m.role}
                  content={m.content}
                  onEditRequest={m.role === 'user' ? handleEditRequest : undefined}
                />
              ) : null}
              </div>
            </div>
            )
          })}
        </div>
        <footer className="composer-footer">
          {(active?.messages?.length ?? 0) >= 2 ? (
            <CopyConversationButton messages={active.messages} />
          ) : null}
          <ProviderToolbar
            activeProviderId={activeSpeakingProviderId}
            onActiveProviderChange={setActiveSpeakingProviderId}
            disabled={loading}
          />
          <div className="composer">
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
          />
          <button type="button" className="council" onClick={council} disabled={loading || !input.trim()}>
            Council
          </button>
          <button type="button" className="send" onClick={send} disabled={loading || !input.trim()}>
            {loading ? '…' : 'Send'}
          </button>
          </div>
        </footer>
      </main>
    </div>
  )
}

export default App
