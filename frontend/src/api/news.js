/**
 * Product News API client (Pass C / Pass D).
 * Package-first projections only — not the internal EventPackage admin surface.
 */
import { BEN_API_BASE } from '../config.js'
import { humanizeBenHttpError, parseBenErrorResponse, readJsonResponse } from './benErrors.js'

/**
 * @typedef {Object} NewsWhyItMattersItem
 * @property {string} text
 * @property {string} [kind]
 */

/**
 * @typedef {Object} NewsHeroImage
 * @property {string} url
 * @property {string|null} [source_article_id]
 * @property {string|null} [origin]
 * @property {number|null} [width]
 * @property {number|null} [height]
 * @property {string|null} [selected_at]
 * @property {string|null} [selection_reason]
 * @property {number|null} [selection_score]
 * @property {number|null} [hero_confidence]
 */

/**
 * @typedef {Object} NewsTopItem
 * @property {number} rank
 * @property {string} event_id
 * @property {string} headline
 * @property {string} summary
 * @property {NewsWhyItMattersItem[]} why_it_matters
 * @property {number} source_count
 * @property {number} article_count
 * @property {string|null} [updated_at]
 * @property {string|null} [happened_at]
 * @property {string} lifecycle
 * @property {boolean} conflict_open
 * @property {string[]} reasons
 * @property {string|null} [image_url]
 * @property {NewsHeroImage|null} [hero_image]
 * @property {string} [locale]
 * @property {boolean} [original_locale_indicator]
 * @property {string|null} [translation_status]
 * @property {Record<string, string>} [field_translation_status]
 */

/**
 * @typedef {Object} NewsTopResponse
 * @property {string} generated_at
 * @property {string} editorial_version
 * @property {NewsTopItem[]} items
 * @property {string} [request_id]
 */

/**
 * @typedef {Object} NewsTopicSource
 * @property {string} source_id
 * @property {string} name
 * @property {string} tier
 * @property {string[]} article_ids
 */

/**
 * @typedef {Object} NewsTopicArticle
 * @property {string} article_id
 * @property {string} title
 * @property {string} url
 * @property {string|null} [published_at]
 * @property {string} source_id
 * @property {string} role
 * @property {string|null} [image_url]
 */

/**
 * @typedef {Object} NewsTopicDetail
 * @property {string} event_id
 * @property {number} package_version
 * @property {number} schema_version
 * @property {string} headline
 * @property {string} summary
 * @property {NewsWhyItMattersItem[]} why_it_matters
 * @property {string} lifecycle
 * @property {boolean} conflict_open
 * @property {string|null} [happened_at]
 * @property {string|null} [updated_at]
 * @property {string|null} [image_url]
 * @property {NewsHeroImage|null} [hero_image]
 * @property {string} [locale]
 * @property {boolean} [original_locale_indicator]
 * @property {string[]} [fallback_fields]
 * @property {NewsTopicSource[]} sources
 * @property {NewsTopicArticle[]} articles
 * @property {object[]} current_facts
 * @property {object[]} conflicts
 * @property {unknown[]} claims
 */

/**
 * @typedef {Object} NewsTopicResponse
 * @property {NewsTopicDetail} topic
 * @property {string} [request_id]
 */

function enrichFetchError(res, data) {
  const err = new Error(humanizeBenHttpError(res.status, data))
  err.status = res.status
  err.data = data
  err.parsed = parseBenErrorResponse(res.status, data)
  return err
}

/** @param {number} [limit] */
/** @param {string} [locale] */
export function newsTopUrl(limit = 10, locale = 'en') {
  const n = Number(limit)
  const safe = Number.isFinite(n) ? Math.max(1, Math.min(50, Math.trunc(n))) : 10
  const loc = String(locale || 'en').trim() || 'en'
  return `${BEN_API_BASE}/api/news/top?limit=${safe}&locale=${encodeURIComponent(loc)}`
}

/** @param {string} eventId */
/** @param {string} [locale] */
export function newsTopicUrl(eventId, locale = 'en') {
  const id = String(eventId || '').trim()
  const loc = String(locale || 'en').trim() || 'en'
  return `${BEN_API_BASE}/api/news/topics/${encodeURIComponent(id)}?locale=${encodeURIComponent(loc)}`
}

/**
 * @param {Record<string, string>} headers
 * @param {{ limit?: number, locale?: string }} [options]
 * @returns {Promise<NewsTopResponse>}
 */
export async function fetchNewsTop(headers, { limit = 10, locale = 'en' } = {}) {
  const res = await fetch(newsTopUrl(limit, locale), { headers })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}

/**
 * @param {string} eventId
 * @param {Record<string, string>} headers
 * @param {{ locale?: string }} [options]
 * @returns {Promise<NewsTopicResponse>}
 */
export async function fetchNewsTopic(eventId, headers, { locale = 'en' } = {}) {
  const res = await fetch(newsTopicUrl(eventId, locale), { headers })
  const data = await readJsonResponse(res)
  if (!res.ok) throw enrichFetchError(res, data)
  return data
}
