import PropTypes from 'prop-types'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchNewsTop, fetchNewsTopic } from '../api/news.js'
import { formatRelativeTime, formatUpdatedLabel } from '../lib/formatRelativeTime.js'
import { isValidEventId, newsFeedPath, newsTopicPath } from '../lib/newsRoutes.js'
import './NewsOverlay.css'

export function NewsNavTrigger({ onOpen, active = false, disabled = false }) {
  return (
    <button
      type="button"
      className={`news-nav-trigger${active ? ' news-nav-trigger--active' : ''}`}
      onClick={onOpen}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-current={active ? 'page' : undefined}
    >
      <span>News</span>
      <span className="news-nav-trigger__chevron" aria-hidden="true">
        ▸
      </span>
    </button>
  )
}

NewsNavTrigger.propTypes = {
  onOpen: PropTypes.func.isRequired,
  active: PropTypes.bool,
  disabled: PropTypes.bool,
}

NewsNavTrigger.defaultProps = {
  active: false,
  disabled: false,
}

function padRank(rank) {
  const n = Number(rank)
  if (!Number.isFinite(n)) return '—'
  return String(Math.trunc(n)).padStart(2, '0')
}

function sourceNameById(sources) {
  /** @type {Map<string, string>} */
  const map = new Map()
  for (const source of sources || []) {
    if (source?.source_id) map.set(String(source.source_id), String(source.name || source.source_id))
  }
  return map
}

function usefulLifecycle(lifecycle) {
  const value = String(lifecycle || '').trim().toLowerCase()
  if (!value || value === 'developing' || value === 'open') return null
  return value
}

/**
 * @param {{
 *   open: boolean,
 *   route: { view: 'feed' | 'detail', eventId: string | null } | null,
 *   onClose: () => void,
 *   onOpenFeed: () => void,
 *   onOpenTopic: (eventId: string) => void,
 *   buildHeaders: () => Promise<Record<string, string>> | Record<string, string>,
 *   disabled?: boolean,
 * }} props
 */
export function NewsOverlay({
  open,
  route,
  onClose,
  onOpenFeed,
  onOpenTopic,
  buildHeaders,
  disabled = false,
}) {
  const [feedState, setFeedState] = useState({
    status: 'idle',
    items: [],
    error: null,
    editorialVersion: null,
  })
  const [topicState, setTopicState] = useState({
    status: 'idle',
    topic: null,
    error: null,
    notFound: false,
  })

  const view = route?.view || 'feed'
  const eventId = route?.eventId || null

  const loadFeed = useCallback(async () => {
    setFeedState((prev) => ({ ...prev, status: 'loading', error: null }))
    try {
      const headers = await buildHeaders()
      const data = await fetchNewsTop(headers, { limit: 10 })
      const items = Array.isArray(data?.items) ? data.items : []
      setFeedState({
        status: 'ready',
        items,
        error: null,
        editorialVersion: data?.editorial_version || null,
      })
    } catch (err) {
      setFeedState({
        status: 'error',
        items: [],
        error: err?.message || 'Could not load news.',
        editorialVersion: null,
      })
    }
  }, [buildHeaders])

  const loadTopic = useCallback(
    async (id) => {
      if (!isValidEventId(id)) {
        setTopicState({
          status: 'error',
          topic: null,
          error: 'This news topic link is invalid.',
          notFound: true,
        })
        return
      }
      setTopicState({ status: 'loading', topic: null, error: null, notFound: false })
      try {
        const headers = await buildHeaders()
        const data = await fetchNewsTopic(id, headers)
        setTopicState({
          status: 'ready',
          topic: data?.topic || null,
          error: null,
          notFound: false,
        })
      } catch (err) {
        const notFound = Number(err?.status) === 404
        setTopicState({
          status: 'error',
          topic: null,
          error: notFound
            ? 'This news topic is no longer available.'
            : err?.message || 'Could not load this topic.',
          notFound,
        })
      }
    },
    [buildHeaders]
  )

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  useEffect(() => {
    if (!open) return
    if (view === 'feed') void loadFeed()
  }, [open, view, loadFeed])

  useEffect(() => {
    if (!open) return
    if (view === 'detail' && eventId) void loadTopic(eventId)
  }, [open, view, eventId, loadTopic])

  const names = useMemo(
    () => sourceNameById(topicState.topic?.sources),
    [topicState.topic]
  )

  if (!open) return null

  const title = view === 'detail' ? 'Topic' : 'Top 10 AI News'

  return (
    <div className="news-overlay" role="dialog" aria-modal="true" aria-labelledby="news-overlay-title">
      <button
        type="button"
        className="news-overlay__scrim"
        aria-label="Close BEN News"
        onClick={onClose}
      />
      <div className="news-overlay__panel">
        <header className="news-overlay__header">
          <div>
            <p className="news-overlay__eyebrow">BEN News</p>
            <h1 id="news-overlay-title" className="news-overlay__title">
              {title}
            </h1>
          </div>
          <button type="button" className="news-overlay__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="news-overlay__body">
          {view === 'feed' ? (
            <FeedView
              state={feedState}
              disabled={disabled}
              onSelect={(id) => onOpenTopic(id)}
              onRetry={loadFeed}
            />
          ) : (
            <DetailView
              state={topicState}
              sourceNames={names}
              onBack={onOpenFeed}
              onRetry={() => eventId && loadTopic(eventId)}
            />
          )}
        </div>
      </div>
    </div>
  )
}

NewsOverlay.propTypes = {
  open: PropTypes.bool.isRequired,
  route: PropTypes.shape({
    view: PropTypes.oneOf(['feed', 'detail']).isRequired,
    eventId: PropTypes.string,
  }),
  onClose: PropTypes.func.isRequired,
  onOpenFeed: PropTypes.func.isRequired,
  onOpenTopic: PropTypes.func.isRequired,
  buildHeaders: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
}

NewsOverlay.defaultProps = {
  route: { view: 'feed', eventId: null },
  disabled: false,
}

function FeedView({ state, disabled, onSelect, onRetry }) {
  if (state.status === 'loading' || state.status === 'idle') {
    return (
      <p className="news-overlay__status" role="status" aria-live="polite">
        Loading top stories…
      </p>
    )
  }

  if (state.status === 'error') {
    return (
      <div className="news-overlay__error" role="alert">
        <p>{state.error}</p>
        <button type="button" className="news-detail__back" onClick={onRetry}>
          Retry
        </button>
      </div>
    )
  }

  if (!state.items.length) {
    return (
      <div className="news-overlay__empty" role="status">
        <p>No ranked news topics are available yet.</p>
        <p>New stories will appear after the next collection and build cycle.</p>
      </div>
    )
  }

  return (
    <ol className="news-feed" aria-label="Top 10 AI News">
      {state.items.map((item) => {
        const updated = formatUpdatedLabel(item.updated_at)
        const lifecycle = usefulLifecycle(item.lifecycle)
        const why = Array.isArray(item.why_it_matters) && item.why_it_matters[0]?.text
          ? String(item.why_it_matters[0].text)
          : null
        return (
          <li key={item.event_id}>
            <button
              type="button"
              className="news-feed__row"
              disabled={disabled}
              onClick={() => onSelect(item.event_id)}
            >
              <span className="news-feed__rank" aria-hidden="true">
                {padRank(item.rank)}
              </span>
              <span>
                <h2 className="news-feed__headline">{item.headline}</h2>
                {item.summary ? <p className="news-feed__summary">{item.summary}</p> : null}
                {why ? <p className="news-feed__why">{why}</p> : null}
                <p className="news-feed__meta">
                  <span>
                    {item.source_count} {item.source_count === 1 ? 'source' : 'sources'}
                  </span>
                  <span aria-hidden="true">·</span>
                  <span>
                    {item.article_count} {item.article_count === 1 ? 'article' : 'articles'}
                  </span>
                  {updated ? (
                    <>
                      <span aria-hidden="true">·</span>
                      <time dateTime={item.updated_at || undefined} title={updated.absolute}>
                        {updated.label}
                      </time>
                    </>
                  ) : null}
                  {lifecycle ? (
                    <>
                      <span aria-hidden="true">·</span>
                      <span className="news-feed__badge">{lifecycle}</span>
                    </>
                  ) : null}
                  {item.conflict_open ? (
                    <>
                      <span aria-hidden="true">·</span>
                      <span className="news-feed__badge news-feed__badge--conflict">
                        Open conflict
                      </span>
                    </>
                  ) : null}
                </p>
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

FeedView.propTypes = {
  state: PropTypes.object.isRequired,
  disabled: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
}

function DetailView({ state, sourceNames, onBack, onRetry }) {
  if (state.status === 'loading' || state.status === 'idle') {
    return (
      <div className="news-detail">
        <button type="button" className="news-detail__back" onClick={onBack}>
          ← Back to Top 10
        </button>
        <p className="news-overlay__status" role="status" aria-live="polite">
          Loading topic…
        </p>
      </div>
    )
  }

  if (state.status === 'error' || !state.topic) {
    return (
      <div className="news-detail">
        <button type="button" className="news-detail__back" onClick={onBack}>
          ← Back to Top 10
        </button>
        <div className="news-overlay__error" role="alert">
          <p>{state.error || 'This news topic is no longer available.'}</p>
          {!state.notFound ? (
            <button type="button" className="news-detail__back" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      </div>
    )
  }

  const topic = state.topic
  const updated = formatUpdatedLabel(topic.updated_at)
  const lifecycle = usefulLifecycle(topic.lifecycle)
  const whyItems = Array.isArray(topic.why_it_matters) ? topic.why_it_matters.filter((w) => w?.text) : []
  const facts = Array.isArray(topic.current_facts) ? topic.current_facts : []
  const conflicts = Array.isArray(topic.conflicts) ? topic.conflicts : []
  const sources = Array.isArray(topic.sources) ? topic.sources : []
  const articles = Array.isArray(topic.articles) ? topic.articles : []
  const roles = new Set(articles.map((a) => a.role).filter(Boolean))
  const showRoles = roles.size > 1

  return (
    <article className="news-detail">
      <button type="button" className="news-detail__back" onClick={onBack}>
        ← Back to Top 10
      </button>
      <h2 className="news-detail__headline">{topic.headline}</h2>
      {topic.summary ? <p className="news-detail__summary">{topic.summary}</p> : null}
      <p className="news-detail__meta">
        <span>
          {sources.length} {sources.length === 1 ? 'source' : 'sources'}
        </span>
        <span aria-hidden="true">·</span>
        <span>
          {articles.length} {articles.length === 1 ? 'article' : 'articles'}
        </span>
        {updated ? (
          <>
            <span aria-hidden="true">·</span>
            <time dateTime={topic.updated_at || undefined} title={updated.absolute}>
              {updated.label}
            </time>
          </>
        ) : null}
        {lifecycle ? (
          <>
            <span aria-hidden="true">·</span>
            <span>{lifecycle}</span>
          </>
        ) : null}
        {topic.conflict_open ? (
          <>
            <span aria-hidden="true">·</span>
            <span className="news-feed__badge news-feed__badge--conflict">Open conflict</span>
          </>
        ) : null}
      </p>

      {whyItems.length ? (
        <section className="news-detail__section" aria-labelledby="news-why-heading">
          <h3 id="news-why-heading" className="news-detail__section-title">
            Why it matters
          </h3>
          <ul className="news-detail__why-list">
            {whyItems.map((item, index) => (
              <li key={`${index}-${item.text.slice(0, 24)}`}>{item.text}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {articles.length ? (
        <section className="news-detail__section" aria-labelledby="news-articles-heading">
          <h3 id="news-articles-heading" className="news-detail__section-title">
            Supporting coverage
          </h3>
          <ul className="news-detail__article-list">
            {articles.map((article) => {
              const published = formatRelativeTime(article.published_at)
              const sourceLabel = sourceNames.get(String(article.source_id)) || 'Source'
              return (
                <li key={article.article_id} className="news-detail__article">
                  <a
                    className="news-detail__article-link"
                    href={article.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {article.title}
                    <span className="news-sr-only"> (opens in new tab)</span>
                  </a>
                  <p className="news-detail__article-meta">
                    {sourceLabel}
                    {published ? ` · ${published.label}` : null}
                    {showRoles && article.role ? ` · ${article.role}` : null}
                  </p>
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}

      {sources.length ? (
        <section className="news-detail__section" aria-labelledby="news-sources-heading">
          <h3 id="news-sources-heading" className="news-detail__section-title">
            Sources
          </h3>
          <ul className="news-detail__source-list">
            {sources.map((source) => (
              <li key={source.source_id}>{source.name}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {facts.length ? (
        <section className="news-detail__section" aria-labelledby="news-facts-heading">
          <h3 id="news-facts-heading" className="news-detail__section-title">
            Current facts
          </h3>
          <ul className="news-detail__fact-list">
            {facts.map((fact, index) => (
              <li key={fact.claim_id || index}>{fact.text || JSON.stringify(fact)}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {conflicts.length ? (
        <section className="news-detail__section" aria-labelledby="news-conflicts-heading">
          <h3 id="news-conflicts-heading" className="news-detail__section-title">
            Conflicts
          </h3>
          <ul className="news-detail__conflict-list">
            {conflicts.map((conflict, index) => (
              <li key={conflict.topic || index}>
                {conflict.topic || 'Disputed topic'}
                {conflict.resolution ? ` (${conflict.resolution})` : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  )
}

DetailView.propTypes = {
  state: PropTypes.object.isRequired,
  sourceNames: PropTypes.instanceOf(Map).isRequired,
  onBack: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
}

// Re-export path helpers for App wiring tests / consumers
export { newsFeedPath, newsTopicPath }
