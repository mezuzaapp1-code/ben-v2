import { useCallback, useMemo, useState } from 'react'
import './App.css'

const TENANT_ID = '00000000-0000-0000-0000-000000000001'
const CHAT_URL = 'https://ben-v2-production.up.railway.app/chat'
const COUNCIL_URL = 'https://ben-v2-production.up.railway.app/council'

const COUNCIL_LABEL = {
  'Legal Advisor': '⚖️ Legal Advisor',
  'Business Advisor': '💼 Business Advisor',
  'Strategy Advisor': '🎯 Strategy Advisor',
}

function App() {
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
      const res = await fetch(CHAT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, tenant_id: TENANT_ID, tier }),
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
  }, [input, loading, activeId, threads, tier, newThread])

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
      const res = await fetch(COUNCIL_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, tenant_id: TENANT_ID }),
      })
      const data = await res.json().catch(() => ({}))
      const members = Array.isArray(data.council) ? data.council : []
      const extras = members.map((c, i) => {
        const name = c.expert || 'Advisor'
        const head = COUNCIL_LABEL[name] || name
        return {
          role: 'assistant',
          content: `${head}: ${c.response ?? ''}`,
          model_used: c.model ?? '',
          cost_usd: i === members.length - 1 ? data.cost_usd ?? 0 : 0,
        }
      })
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
  }, [input, loading, activeId, threads, newThread])

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">BEN</div>
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
            <div key={i} className={`bubble-wrap ${m.role}`}>
              <div className={`bubble ${m.role}`}>
                <div className="bubble-text">{m.content}</div>
                {m.role === 'assistant' && (m.model_used || m.cost_usd !== undefined) && (
                  <div className="meta">
                    {m.model_used && <span>{m.model_used}</span>}
                    {m.model_used && <span className="dot">·</span>}
                    <span>${Number(m.cost_usd).toFixed(6)}</span>
                  </div>
                )}
              </div>
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
