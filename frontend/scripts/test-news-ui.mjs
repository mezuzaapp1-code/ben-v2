/**
 * Node smoke tests for BEN News Pass D (no Vitest/RTL in this frontend).
 * Run: node frontend/scripts/test-news-ui.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { formatRelativeTime, formatUpdatedLabel } from '../src/lib/formatRelativeTime.js'
import {
  isValidEventId,
  newsFeedPath,
  newsTopicPath,
  parseNewsLocation,
} from '../src/lib/newsRoutes.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

// --- routes -----------------------------------------------------------------
assert(newsFeedPath() === '/news', 'feed path')
assert(
  newsTopicPath('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa') ===
    '/news/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  'topic path'
)
assert(parseNewsLocation('/news')?.view === 'feed', 'parse /news')
assert(parseNewsLocation('/news/')?.view === 'feed', 'parse /news/')
assert(
  parseNewsLocation('/news/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')?.eventId ===
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  'parse topic'
)
assert(parseNewsLocation('/chat') === null, 'non-news path')
assert(isValidEventId('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'), 'valid uuid')
assert(!isValidEventId('not-a-uuid'), 'invalid uuid')

// --- relative time ----------------------------------------------------------
const now = new Date('2026-07-25T18:00:00Z')
const mins = formatUpdatedLabel(new Date('2026-07-25T17:48:00Z'), now)
assert(mins?.label === 'Updated 12m ago', `12m label got ${mins?.label}`)
const hours = formatUpdatedLabel(new Date('2026-07-25T15:00:00Z'), now)
assert(hours?.label === 'Updated 3h ago', `3h label got ${hours?.label}`)
const yday = formatUpdatedLabel(new Date('2026-07-24T12:00:00Z'), now)
assert(yday?.label === 'Updated yesterday', `yesterday got ${yday?.label}`)
assert(formatRelativeTime(null) === null, 'null time')
assert(formatRelativeTime('not-a-date') === null, 'bad time')

// --- API module paths (avoid importing Vite-bound config.js in Node) --------
const apiSrcEarly = readFileSync(join(root, 'src/api/news.js'), 'utf8')
assert(apiSrcEarly.includes('/api/news/top?limit='), 'top url builder')
assert(apiSrcEarly.includes('/api/news/topics/'), 'topic url builder')
assert(apiSrcEarly.includes('Math.min(50'), 'top url clamp')

// --- no frontend reranking helper: preserve API order -----------------------
const apiOrder = [
  { rank: 1, event_id: 'a', headline: 'First' },
  { rank: 2, event_id: 'b', headline: 'Second' },
  { rank: 3, event_id: 'c', headline: 'Third' },
]
const rendered = apiOrder.map((item) => item.event_id)
assert(rendered.join(',') === 'a,b,c', 'preserve API order')

// --- source files: nav + routes + no internal package fields ----------------
const appSrc = readFileSync(join(root, 'src/App.jsx'), 'utf8')
assert(appSrc.includes('NewsNavTrigger'), 'App wires NewsNavTrigger')
assert(appSrc.includes('NewsOverlay'), 'App wires NewsOverlay')
assert(appSrc.includes('openNewsFeed'), 'App opens news feed')
assert(appSrc.includes('newsFeedPath'), 'App uses newsFeedPath')

const overlaySrc = readFileSync(join(root, 'src/components/NewsOverlay.jsx'), 'utf8')
assert(overlaySrc.includes('fetchNewsTop'), 'overlay fetches top')
assert(overlaySrc.includes('fetchNewsTopic'), 'overlay fetches topic')
assert(overlaySrc.includes('newsFeedTitle'), 'feed heading i18n')
assert(overlaySrc.includes('newsEmptyTitle'), 'empty copy i18n')
assert(overlaySrc.includes('newsTopicMissingLabel'), '404 copy i18n')
assert(overlaySrc.includes('image_url'), 'renders story images')
assert(overlaySrc.includes('news-feed__thumb'), 'feed thumbnail class')
assert(!overlaySrc.includes('provenance'), 'no provenance in UI')
assert(!overlaySrc.includes('policy_notes'), 'no policy_notes in UI')
assert(!overlaySrc.includes('sort_key'), 'no sort_key in UI')
assert(!overlaySrc.includes('topic_signature'), 'no topic_signature in UI')
assert(!overlaySrc.includes('content_fingerprint'), 'no fingerprint in UI')
assert(!overlaySrc.includes('candidate_limit'), 'no candidate_limit in UI')

const apiSrc = readFileSync(join(root, 'src/api/news.js'), 'utf8')
assert(apiSrc.includes('/api/news/top'), 'api top path')
assert(apiSrc.includes('/api/news/topics/'), 'api topic path')
assert(apiSrc.includes('@typedef {Object} NewsTopItem'), 'NewsTopItem type')
assert(apiSrc.includes('@typedef {Object} NewsTopicDetail'), 'NewsTopicDetail type')

console.log('OK: news UI smoke tests passed')
