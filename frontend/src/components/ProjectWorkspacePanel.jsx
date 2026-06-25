import { useCallback, useEffect, useMemo, useState } from 'react'
import { exportLedger, fetchProjectLedger, fetchProjects } from '../api/projects.js'
import { buildBenHeaders } from '../api/benHeaders.js'

function ledgerTotals(entries) {
  let income = 0
  let expense = 0
  for (const e of entries || []) {
    const amt = Number(e.amount) || 0
    if (e.entry_type === 'INCOME') income += amt
    else expense += amt
  }
  return { income, expense, net: income - expense }
}

export function ProjectWorkspacePanel({
  getToken,
  activeProjectId,
  onProjectChange,
  onClose,
  onExportReport,
}) {
  const [projects, setProjects] = useState([])
  const [ledger, setLedger] = useState([])
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState(null)

  const totals = useMemo(() => ledgerTotals(ledger), [ledger])

  const refreshLedger = useCallback(async () => {
    if (!activeProjectId || !getToken) return
    setError(null)
    try {
      const headers = await buildBenHeaders(getToken)
      const data = await fetchProjectLedger(activeProjectId, headers)
      setLedger(data.entries || data || [])
    } catch (e) {
      setError(e.message || 'Could not load ledger')
      setLedger([])
    }
  }, [activeProjectId, getToken])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (!getToken) {
        setLoading(false)
        return
      }
      setLoading(true)
      setError(null)
      try {
        const headers = await buildBenHeaders(getToken)
        const data = await fetchProjects(headers)
        if (cancelled) return
        const list = data.projects || data || []
        setProjects(list)
        if (!activeProjectId && list[0]?.id) {
          onProjectChange(list[0].id)
        }
      } catch (e) {
        if (!cancelled) setError(e.message || 'Could not load projects')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [getToken, activeProjectId, onProjectChange])

  useEffect(() => {
    if (activeProjectId) void refreshLedger()
  }, [activeProjectId, refreshLedger])

  const handleExport = async () => {
    if (!activeProjectId || !getToken) return
    setExporting(true)
    setError(null)
    try {
      const headers = await buildBenHeaders(getToken)
      const report = await exportLedger(activeProjectId, { format: 'summary' }, headers)
      onExportReport?.(report)
    } catch (e) {
      setError(e.message || 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <aside className="project-workspace" aria-label="Project workspace">
      <header className="project-workspace__header">
        <h2 className="project-workspace__title">Project workspace</h2>
        <button type="button" className="project-workspace__close" onClick={onClose} aria-label="Close workspace">
          ×
        </button>
      </header>

      {loading ? <p className="project-workspace__hint">Loading projects…</p> : null}
      {error ? <p className="project-workspace__error">{error}</p> : null}

      <label className="project-workspace__label">
        Active project
        <select
          className="project-workspace__select"
          value={activeProjectId || ''}
          onChange={(e) => onProjectChange(e.target.value)}
          disabled={!projects.length}
        >
          {!projects.length ? <option value="">No projects</option> : null}
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

      <section className="project-workspace__summary">
        <h3>Ledger summary</h3>
        <ul>
          <li>
            <span>Income</span>
            <strong>{totals.income.toFixed(2)}</strong>
          </li>
          <li>
            <span>Expense</span>
            <strong>{totals.expense.toFixed(2)}</strong>
          </li>
          <li>
            <span>Net</span>
            <strong>{totals.net.toFixed(2)}</strong>
          </li>
        </ul>
        <p className="project-workspace__hint">{ledger.length} entries</p>
      </section>

      <div className="project-workspace__actions">
        <button type="button" onClick={() => void refreshLedger()} disabled={!activeProjectId}>
          Refresh
        </button>
        <button type="button" onClick={() => void handleExport()} disabled={!activeProjectId || exporting}>
          {exporting ? 'Exporting…' : 'Export to accountant'}
        </button>
      </div>

      <section className="project-workspace__ledger">
        <h3>Recent entries</h3>
        {ledger.length === 0 ? (
          <p className="project-workspace__hint">No ledger entries yet. Capture a receipt in chat.</p>
        ) : (
          <ul>
            {ledger.slice(-8).reverse().map((e) => (
              <li key={e.id}>
                <span className={`project-workspace__entry-type project-workspace__entry-type--${(e.entry_type || '').toLowerCase()}`}>
                  {e.entry_type}
                </span>
                <span>{Number(e.amount).toFixed(2)} {e.currency}</span>
                <span className="project-workspace__entry-desc">{e.description || '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  )
}
