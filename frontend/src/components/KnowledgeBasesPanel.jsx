import './KnowledgeBasesPanel.css'

const CONTAINED_COPY =
  'Legacy Knowledge bases are unavailable. Existing templates were not deleted.'

export function KnowledgeBasesPanel({
  buildHeaders: _buildHeaders,
  disabled = false,
  embedded = false,
}) {
  return (
    <section className={`kb-panel kb-panel--contained${embedded ? ' kb-panel--embedded' : ''}`}>
      <p className="kb-panel__toggle" aria-disabled={disabled ? 'true' : undefined}>
        <span>מאגרים · Knowledge</span>
      </p>
      <p className="kb-panel__hint">{CONTAINED_COPY}</p>
    </section>
  )
}
