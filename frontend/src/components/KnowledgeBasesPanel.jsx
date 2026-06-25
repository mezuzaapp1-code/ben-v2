import { useCallback, useEffect, useState } from 'react'
import {
  addKnowledgeDocument,
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteKnowledgeDocument,
  fetchKnowledgeBases,
  fetchKnowledgeDocuments,
} from '../api/knowledge.js'
import './KnowledgeBasesPanel.css'

export function KnowledgeBasesPanel({ buildHeaders, disabled = false, embedded = false }) {
  const [open, setOpen] = useState(false)
  const [bases, setBases] = useState([])
  const [activeBaseId, setActiveBaseId] = useState(null)
  const [documents, setDocuments] = useState([])
  const [newBaseName, setNewBaseName] = useState('')
  const [docTitle, setDocTitle] = useState('')
  const [docContent, setDocContent] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const loadBases = useCallback(async () => {
    if (!buildHeaders) return
    const headers = await buildHeaders()
    const data = await fetchKnowledgeBases(headers)
    setBases(data.bases || [])
  }, [buildHeaders])

  const loadDocuments = useCallback(
    async (baseId) => {
      if (!buildHeaders || !baseId) {
        setDocuments([])
        return
      }
      const headers = await buildHeaders()
      const data = await fetchKnowledgeDocuments(baseId, headers)
      setDocuments(data.documents || [])
    },
    [buildHeaders]
  )

  useEffect(() => {
    if (!open) return
    void loadBases().catch(() => setBases([]))
  }, [open, loadBases])

  useEffect(() => {
    if (!activeBaseId) {
      setDocuments([])
      return
    }
    void loadDocuments(activeBaseId).catch(() => setDocuments([]))
  }, [activeBaseId, loadDocuments])

  const run = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e?.message || 'Knowledge base action failed')
    } finally {
      setBusy(false)
    }
  }

  const handleCreateBase = () =>
    run(async () => {
      const name = newBaseName.trim()
      if (!name) return
      const headers = await buildHeaders()
      const created = await createKnowledgeBase(name, headers)
      setNewBaseName('')
      await loadBases()
      setActiveBaseId(created.id)
    })

  const handleAddDocument = () =>
    run(async () => {
      if (!activeBaseId || !docContent.trim()) return
      const headers = await buildHeaders()
      await addKnowledgeDocument(
        activeBaseId,
        { title: docTitle.trim() || 'Template', content: docContent },
        headers
      )
      setDocTitle('')
      setDocContent('')
      await loadDocuments(activeBaseId)
    })

  const activeBase = bases.find((b) => b.id === activeBaseId)

  return (
    <section
      className={`kb-panel${open ? ' kb-panel--open' : ''}${embedded ? ' kb-panel--embedded' : ''}`}
    >
      <button
        type="button"
        className="kb-panel__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span>מאגרים · Knowledge</span>
        <span className="kb-panel__chevron" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open ? (
        <div className="kb-panel__body">
          <p className="kb-panel__hint">
            Paste gold templates here. Refer by name in chat (e.g. &quot;build RMS based on the RMS base&quot;).
          </p>
          {error ? <p className="kb-panel__error">{error}</p> : null}
          <div className="kb-panel__row">
            <input
              className="kb-panel__input"
              type="text"
              placeholder="New base name (RMS, Templates…)"
              value={newBaseName}
              disabled={disabled || busy}
              onChange={(e) => setNewBaseName(e.target.value)}
            />
            <button
              type="button"
              className="kb-panel__btn"
              disabled={disabled || busy || !newBaseName.trim()}
              onClick={() => void handleCreateBase()}
            >
              +
            </button>
          </div>
          <div className="kb-panel__bases" role="listbox" aria-label="Knowledge bases">
            {bases.map((base) => (
              <div key={base.id} className="kb-panel__base-row">
                <button
                  type="button"
                  role="option"
                  aria-selected={base.id === activeBaseId}
                  className={
                    base.id === activeBaseId
                      ? 'kb-panel__base kb-panel__base--active'
                      : 'kb-panel__base'
                  }
                  onClick={() => setActiveBaseId(base.id)}
                >
                  {base.name}
                </button>
                <button
                  type="button"
                  className="kb-panel__btn kb-panel__btn--ghost"
                  disabled={disabled || busy}
                  aria-label={`Delete ${base.name}`}
                  onClick={() =>
                    void run(async () => {
                      const headers = await buildHeaders()
                      await deleteKnowledgeBase(base.id, headers)
                      if (activeBaseId === base.id) setActiveBaseId(null)
                      await loadBases()
                    })
                  }
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          {activeBase ? (
            <div className="kb-panel__docs">
              <span className="kb-panel__docs-title">{activeBase.name} documents</span>
              <ul className="kb-panel__doc-list">
                {documents.map((doc) => (
                  <li key={doc.id} className="kb-panel__doc-item">
                    <span className="kb-panel__doc-label">{doc.title}</span>
                    <button
                      type="button"
                      className="kb-panel__btn kb-panel__btn--ghost"
                      disabled={disabled || busy}
                      onClick={() =>
                        void run(async () => {
                          const headers = await buildHeaders()
                          await deleteKnowledgeDocument(doc.id, headers)
                          await loadDocuments(activeBaseId)
                        })
                      }
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
              <input
                className="kb-panel__input"
                type="text"
                placeholder="Document title"
                value={docTitle}
                disabled={disabled || busy}
                onChange={(e) => setDocTitle(e.target.value)}
              />
              <textarea
                className="kb-panel__textarea"
                placeholder="Paste template content…"
                value={docContent}
                disabled={disabled || busy}
                rows={4}
                onChange={(e) => setDocContent(e.target.value)}
              />
              <button
                type="button"
                className="kb-panel__btn kb-panel__btn--wide"
                disabled={disabled || busy || !docContent.trim()}
                onClick={() => void handleAddDocument()}
              >
                Add document
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
