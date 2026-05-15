import { SignInButton, SignOutButton, useAuth } from '@clerk/clerk-react'
import { useCallback, useMemo, useState } from 'react'
import { buildBenHeaders } from './api/benHeaders.js'
import { useBenAuthContext } from './auth/BenAuthContext.jsx'
import { BEN_API_BASE, DEFAULT_TENANT_ID } from './config.js'
import './App.css'

const CHAT_URL = `${BEN_API_BASE}/chat`
const COUNCIL_URL = `${BEN_API_BASE}/council`
const HAS_CLERK_UI = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())

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

function ClerkAuthControls() {
  const { isSignedIn } = useAuth()
  if (!HAS_CLERK_UI) return null
  return (
    <div className="auth-controls">
      {isSignedIn ? (
        <SignOutButton>
          <button type="button" className="auth-btn">
            Sign out
          </button>
        </SignOutButton>
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

  const active = useMemo(
    () => threads.find((t) => t.id === activeId) ?? null,
    [threads, activeId]
  )

  const newThread = useCallback(() => {
    const id = crypto.randomUUID()
    const t = { id, title: 'New conversation', messages: [] }
    setThreads((prev) => [t, ...prev])
    setActiveId(id)
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
      const res = await fetch(CHAT_URL, {
        method: 'POST',
        headers,
        body: JSON.stringify({ message: text, tenant_id: DEFAULT_TENANT_ID, tier }),
      })
      const data = await res.json().catch(() => ({}))
      const assistant = {
        role: 'assistant',
        content: data.response ?? JSON.stringify(data),
        model_used: data.model_used ?? '',
        cost_usd: data.cost_usd ?? 0,
      }
      const serverTid = data.thread_id
      setThreads((prev) =>
        prev.map((t) => {
          if (t.id !== tid) return t
          const next = { ...t, messages: [...t.messages, assistant] }
          if (serverTid && serverTid !== tid) {
            next.id = serverTid
            setActiveId(serverTid)
          }
          return next
        })
      )
    } catch (e) {
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  { role: 'assistant', content: String(e), model_used: '', cost_usd: 0 },
                ],
              }
            : t
        )
      )
    } finally {
      setLoading(false)
    }
  }, [input, loading, activeId, threads, tier, newThread, getToken])

  const council = useCallback(async () => {
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
      const res = await fetch(COUNCIL_URL, {
        method: 'POST',
        headers,
        body: JSON.stringify({ question: text, tenant_id: DEFAULT_TENANT_ID }),
      })
      const data = await res.json().catch(() => ({}))
      const members = Array.isArray(data.council) ? data.council : []
      const syn = data.synthesis && typeof data.synthesis === 'object' ? data.synthesis : null
      const anyExpertFailed = members.some((c) => c.outcome && c.outcome !== 'ok')
      const extras = members.map((c, i) => {
        const name = c.expert || 'Advisor'
        const head = COUNCIL_LABEL[name] || name
        const lastExpert = i === members.length - 1 && !syn
        const statusLabel = expertStatusLabel(c.outcome, c.response)
        return {
          role: 'assistant',
          content: `${head}: ${c.response ?? ''}`,
          model_used: c.model ?? '',
          expert_outcome: c.outcome ?? 'ok',
          expert_status: statusLabel,
          cost_usd: lastExpert ? data.cost_usd ?? 0 : 0,
        }
      })
      if (syn) {
        extras.push({
          role: 'assistant',
          kind: 'council_synthesis',
          synthesis: syn,
          content: councilSynthesisBubbleText(syn, anyExpertFailed),
          model_used: 'synthesis',
          cost_usd: data.cost_usd ?? 0,
        })
      }
      setThreads((prev) =>
        prev.map((t) => (t.id === tid ? { ...t, messages: [...t.messages, ...extras] } : t))
      )
    } catch (e) {
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [...t.messages, { role: 'assistant', content: String(e), model_used: '', cost_usd: 0 }],
              }
            : t
        )
      )
    } finally {
      setLoading(false)
    }
  }, [input, loading, activeId, threads, newThread, getToken])

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
                onClick={() => setActiveId(t.id)}
              >
                {t.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="main">
        <div className="messages">
          {(active?.messages ?? []).map((m, i) => (
            <div
              key={i}
              className={`bubble-wrap ${m.role}${m.kind === 'council_synthesis' ? ' synthesis-wrap' : ''}`}
            >
              {m.kind === 'council_synthesis' && m.synthesis ? (
                <div className="bubble synthesis">
                  <div className="bubble-text">{m.content}</div>
                  {(m.model_used || m.cost_usd !== undefined) && (
                    <div className="meta">
                      {m.model_used && <span>{m.model_used}</span>}
                      {m.model_used && <span className="dot">·</span>}
                      <span>${Number(m.cost_usd).toFixed(6)}</span>
                    </div>
                  )}
                </div>
              ) : (
                <div className={`bubble ${m.role}`}>
                  <div className="bubble-text">{m.content}</div>
                  {m.role === 'assistant' && (m.model_used || m.cost_usd !== undefined || m.expert_status) && (
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
        </footer>
      </main>
    </div>
  )
}

export default App
