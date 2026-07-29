import PropTypes from 'prop-types'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchNewsTop, fetchNewsTopic } from '../api/news.js'
import { useUiLocale } from '../hooks/useUiLocale.js'
import { formatRelativeTime, formatUpdatedLabel } from '../lib/formatRelativeTime.js'
import { isValidEventId, newsFeedPath, newsTopicPath } from '../lib/newsRoutes.js'
import {
  newsArticleCountLabel,
  newsBackToFeedLabel,
  newsCloseLabel,
  newsCloseNewsLabel,
  newsConflictsHeading,
  newsCoverageHeading,
  newsDefaultSourceLabel,
  newsEmptyHint,
  newsEmptyTitle,
  newsEyebrowLabel,
  newsFactsHeading,
  newsFeedTitle,
  newsImageAlt,
  newsLoadingFeedLabel,
  newsLoadingTopicLabel,
  newsOpenConflictLabel,
  newsOpensInNewTabLabel,
  newsRetryLabel,
  newsSourceCountLabel,
  newsSourcesHeading,
  newsTopicMissingLabel,
  newsTopicTitle,
  newsWhyHeading,
} from '../lib/uiStrings.js'
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
  const locale = useUiLocale()

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
          error: newsTopicMissingLabel(locale),
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
            ? newsTopicMissingLabel(locale)
            : err?.message || 'Could not load this topic.',
          notFound,
        })
      }
    },
    [buildHeaders, locale]
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

  const title = view === 'detail' ? newsTopicTitle(locale) : newsFeedTitle(locale)

  return (
    <div
      className="news-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="news-overlay-title"
      lang={locale}
      dir={locale === 'he' ? 'rtl' : 'ltr'}
    >
      <button
        type="button"
        className="news-overlay__scrim"
        aria-label={newsCloseNewsLabel(locale)}
        onClick={onClose}
      />
      <div className="news-overlay__panel">
        <header className="news-overlay__header">
          <div>
            <p className="news-overlay__eyebrow">{newsEyebrowLabel(locale)}</p>
            <h1 id="news-overlay-title" className="news-overlay__title">
              {title}
            </h1>
          </div>
          <button
            type="button"
            className="news-overlay__close"
            onClick={onClose}
            aria-label={newsCloseLabel(locale)}
          >
            ×
          </button>
        </header>

        <div className="news-overlay__body">
          {view === 'feed' ? (
            <FeedView
              state={feedState}
              disabled={disabled}
              locale={locale}
              onSelect={(id) => onOpenTopic(id)}
              onRetry={loadFeed}
            />
          ) : (
            <DetailView
              state={topicState}
              sourceNames={names}
              locale={locale}
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

function FeedView({ state, disabled, locale, onSelect, onRetry }) {
  if (state.status === 'loading' || state.status === 'idle') {
    return (
      <p className="news-overlay__status" role="status" aria-live="polite">
        {newsLoadingFeedLabel(locale)}
      </p>
    )
  }

  if (state.status === 'error') {
    return (
      <div className="news-overlay__error" role="alert">
        <p>{state.error}</p>
        <button type="button" className="news-detail__back" onClick={onRetry}>
          {newsRetryLabel(locale)}
        </button>
      </div>
    )
  }

  if (!state.items.length) {
    return (
      <div className="news-overlay__empty" role="status">
        <p>{newsEmptyTitle(locale)}</p>
        <p>{newsEmptyHint(locale)}</p>
      </div>
    )
  }

  return (
    <ol className="news-feed" aria-label={newsFeedTitle(locale)}>
      {state.items.map((item) => {
        const updated = formatUpdatedLabel(item.updated_at, undefined, locale)
        const lifecycle = usefulLifecycle(item.lifecycle)
        const why = Array.isArray(item.why_it_matters) && item.why_it_matters[0]?.text
          ? String(item.why_it_matters[0].text)
          : null
        const imageUrl = typeof item.image_url === 'string' ? item.image_url.trim() : ''
        return (
          <li key={item.event_id}>
            <button
              type="button"
              className={`news-feed__row${imageUrl ? ' news-feed__row--with-media' : ''}`}
              disabled={disabled}
              onClick={() => onSelect(item.event_id)}
            >
              <span className="news-feed__rank" aria-hidden="true">
                {padRank(item.rank)}
              </span>
              <span className="news-feed__copy">
                <h2 className="news-feed__headline">{item.headline}</h2>
                {item.summary ? <p className="news-feed__summary">{item.summary}</p> : null}
                {why ? <p className="news-feed__why">{why}</p> : null}
                <p className="news-feed__meta">
                  <span>{newsSourceCountLabel(item.source_count, locale)}</span>
                  <span aria-hidden="true">·</span>
                  <span>{newsArticleCountLabel(item.article_count, locale)}</span>
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
                        {newsOpenConflictLabel(locale)}
                      </span>
                    </>
                  ) : null}
                </p>
              </span>
              {imageUrl ? (
                <span className="news-feed__media" aria-hidden="true">
                  <img
                    className="news-feed__thumb"
                    src={imageUrl}
                    alt=""
                    loading="lazy"
                    decoding="async"
                    referrerPolicy="no-referrer"
                  />
                </span>
              ) : null}
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
  locale: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
}

FeedView.defaultProps = {
  locale: 'en',
}

function DetailView({ state, sourceNames, locale, onBack, onRetry }) {
  if (state.status === 'loading' || state.status === 'idle') {
    return (
      <div className="news-detail">
        <button type="button" className="news-detail__back" onClick={onBack}>
          {newsBackToFeedLabel(locale)}
        </button>
        <p className="news-overlay__status" role="status" aria-live="polite">
          {newsLoadingTopicLabel(locale)}
        </p>
      </div>
    )
  }

  if (state.status === 'error' || !state.topic) {
    return (
      <div className="news-detail">
        <button type="button" className="news-detail__back" onClick={onBack}>
          {newsBackToFeedLabel(locale)}
        </button>
        <div className="news-overlay__error" role="alert">
          <p>{state.error || newsTopicMissingLabel(locale)}</p>
          {!state.notFound ? (
            <button type="button" className="news-detail__back" onClick={onRetry}>
              {newsRetryLabel(locale)}
            </button>
          ) : null}
        </div>
      </div>
    )
  }

  const topic = state.topic
  const updated = formatUpdatedLabel(topic.updated_at, undefined, locale)
  const lifecycle = usefulLifecycle(topic.lifecycle)
  const whyItems = Array.isArray(topic.why_it_matters) ? topic.why_it_matters.filter((w) => w?.text) : []
  const facts = Array.isArray(topic.current_facts) ? topic.current_facts : []
  const conflicts = Array.isArray(topic.conflicts) ? topic.conflicts : []
  const sources = Array.isArray(topic.sources) ? topic.sources : []
  const articles = Array.isArray(topic.articles) ? topic.articles : []
  const roles = new Set(articles.map((a) => a.role).filter(Boolean))
  const showRoles = roles.size > 1
  const heroUrl = typeof topic.image_url === 'string' ? topic.image_url.trim() : ''

  return (
    <article className="news-detail">
      <button type="button" className="news-detail__back" onClick={onBack}>
        {newsBackToFeedLabel(locale)}
      </button>
      {heroUrl ? (
        <div className="news-detail__hero">
          <img
            className="news-detail__hero-img"
            src={heroUrl}
            alt={newsImageAlt(topic.headline, locale)}
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
          />
        </div>
      ) : null}
      <h2 className="news-detail__headline">{topic.headline}</h2>
      {topic.summary ? <p className="news-detail__summary">{topic.summary}</p> : null}
      <p className="news-detail__meta">
        <span>{newsSourceCountLabel(sources.length, locale)}</span>
        <span aria-hidden="true">·</span>
        <span>{newsArticleCountLabel(articles.length, locale)}</span>
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
            <span className="news-feed__badge news-feed__badge--conflict">
              {newsOpenConflictLabel(locale)}
            </span>
          </>
        ) : null}
      </p>

      {whyItems.length ? (
        <section className="news-detail__section" aria-labelledby="news-why-heading">
          <h3 id="news-why-heading" className="news-detail__section-title">
            {newsWhyHeading(locale)}
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
            {newsCoverageHeading(locale)}
          </h3>
          <ul className="news-detail__article-list">
            {articles.map((article) => {
              const published = formatRelativeTime(article.published_at, undefined, locale)
              const sourceLabel =
                sourceNames.get(String(article.source_id)) || newsDefaultSourceLabel(locale)
              const thumb = typeof article.image_url === 'string' ? article.image_url.trim() : ''
              return (
                <li key={article.article_id} className="news-detail__article">
                  <a
                    className="news-detail__article-link"
                    href={article.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {thumb ? (
                      <img
                        className="news-detail__article-thumb"
                        src={thumb}
                        alt=""
                        loading="lazy"
                        decoding="async"
                        referrerPolicy="no-referrer"
                      />
                    ) : null}
                    <span className="news-detail__article-copy">
                      <span className="news-detail__article-title">{article.title}</span>
                      <span className="news-sr-only">{newsOpensInNewTabLabel(locale)}</span>
                      <span className="news-detail__article-meta">
                        {sourceLabel}
                        {published ? ` · ${published.label}` : null}
                        {showRoles && article.role ? ` · ${article.role}` : null}
                      </span>
                    </span>
                  </a>
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}

      {sources.length ? (
        <section className="news-detail__section" aria-labelledby="news-sources-heading">
          <h3 id="news-sources-heading" className="news-detail__section-title">
            {newsSourcesHeading(locale)}
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
            {newsFactsHeading(locale)}
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
            {newsConflictsHeading(locale)}
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
  locale: PropTypes.string,
  onBack: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
}

DetailView.defaultProps = {
  locale: 'en',
}

// Re-export path helpers for App wiring tests / consumers
export { newsFeedPath, newsTopicPath }
