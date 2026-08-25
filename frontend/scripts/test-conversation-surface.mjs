/**
 * Conversation surface UX contract: one column, content-only bidi, shared width.
 * Run: node frontend/scripts/test-conversation-surface.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { isClerkPersistentSessionReady } from '../src/auth/clerkPersistentAccess.js'
import {
  collectNodeText,
  getMessageTextDirection,
  isRtlMarkdown,
} from '../src/lib/markdownDirection.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function read(rel) {
  return readFileSync(join(root, rel), 'utf8')
}

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const theme = read('src/theme/theme.css')
const appCss = read('src/App.css')
const appJsx = read('src/App.jsx')
const markdownCss = read('src/components/ChatMarkdown.css')
const markdownJsx = read('src/components/ChatMarkdown.jsx')
const direction = read('src/lib/markdownDirection.js')
const bubble = read('src/components/FileLifecycleStatus.jsx')
const bubbleCss = read('src/components/FileLifecycleStatus.css')
const auth = read('src/auth/clerkPersistentAccess.js')

assert(
  /--conversation-max-width:\s*48rem/.test(theme),
  'shared conversationMaxWidth token is 48rem'
)
assert(
  (theme.match(/--conversation-max-width:/g) || []).length === 1,
  'conversation max width is defined once'
)
assert(appCss.includes('max-width: var(--conversation-max-width)'), 'channel uses the shared token')
assert(
  (appCss.match(/max-width: var\(--conversation-max-width\)/g) || []).length === 1,
  'only the conversation channel owns max-width via the token'
)
assert(
  !appCss.includes('72ch') && !appJsx.includes('72ch') && !bubbleCss.includes('72ch'),
  'no leftover 72ch message widths'
)
assert(!appCss.includes('min(440px') && !appJsx.includes('min(440px'), 'no leftover 440px action-card widths')
assert(!appCss.includes('bubble-wrap--rtl') && !appJsx.includes('bubble-wrap--rtl'), 'no structural RTL wrap class')
assert(!appCss.includes('bubble-wrap--ltr'), 'no structural LTR wrap class')
assert(!appJsx.includes('bubble-stack'), 'duplicate bubble-stack wrapper is gone')
assert(!appCss.includes('.bubble-stack'), 'bubble-stack CSS is gone')

assert(appJsx.includes('className="chat-centered-channel"'), 'messages sit in the shared channel')
assert(
  /composer-footer[\s\S]*chat-centered-channel/.test(appJsx),
  'composer sits in the same shared channel'
)
assert(appCss.includes('.composer-footer {'), 'composer footer is owned by App layout')
assert(
  /position:\s*absolute[\s\S]*left:\s*0[\s\S]*right:\s*0[\s\S]*bottom:\s*0/.test(appCss),
  'composer spans remaining main width after sidebar'
)

assert(!/bubble-wrap[^>]{0,200}dir=/.test(appJsx), 'bubble wrap never carries content dir')
assert(/<div className="bubble-text" dir=\{messageDir\}>/.test(appJsx), 'plain text dir is content-level')
assert(appJsx.includes('dir={getMessageTextDirection(part.text)}'), 'Large Paste text parts get per-part dir')

{
  const renderStart = appJsx.indexOf('shouldRenderAssistantMarkdown')
  const assistantBlock = appJsx.slice(renderStart, appJsx.indexOf('<MessageActionBar', renderStart))
  const usedAt = assistantBlock.indexOf('className="used-files"')
  const metaAt = assistantBlock.lastIndexOf('className="meta"')
  const actionAt = appJsx.indexOf('<MessageActionBar', renderStart)
  const metaSrcAt = appJsx.lastIndexOf('className="meta"', actionAt)
  assert(usedAt >= 0 && metaAt > usedAt, 'assistant anatomy: used files before provider meta')
  assert(actionAt > metaSrcAt && metaSrcAt > renderStart, 'assistant anatomy: actions after meta')
}

assert(/direction:\s*ltr/.test(appCss.match(/\.message-action-bar \{[\s\S]*?\}/)[0]), 'action bar is LTR isolated')
assert(/unicode-bidi:\s*isolate/.test(appCss.match(/\.message-action-bar \{[\s\S]*?\}/)[0]), 'action bar uses bidi isolate')
assert(/direction:\s*ltr/.test(appCss.match(/\.meta \{[\s\S]*?\}/)[0]), 'provider meta is LTR isolated')
assert(/direction:\s*ltr/.test(appCss.match(/\.used-files \{[\s\S]*?\}/)[0]), 'used files chrome is LTR isolated')

assert(/\.bubble\.assistant \{[\s\S]*background:\s*transparent/.test(appCss), 'assistant reads as document, not a card')
assert(/\.bubble\.assistant \{[\s\S]*border:\s*none/.test(appCss), 'assistant has no card border')
assert(/\.bubble\.user \{[\s\S]*background:\s*var\(--ben-user-bubble-bg\)/.test(appCss), 'user turns stay visually distinct')
assert(appCss.includes('border-left: 3px solid'), 'user accent uses physical left, not inline-start')
assert(appJsx.includes("borderLeft: `3px solid ${providerAccent}`"), 'adhoc accent is physical, not bidi-relative')

assert(markdownJsx.includes('DirectedBlock'), 'markdown direction is per block')
assert(markdownJsx.includes('dir={dir}'), 'block tags receive content dir')
assert(!markdownJsx.includes('chat-markdown--rtl'), 'markdown root is not structurally RTL')
assert(markdownJsx.includes('pre dir="ltr"'), 'code fences are isolated LTR')
assert(markdownJsx.includes('<a dir="ltr"'), 'URLs are isolated LTR')
assert(markdownJsx.includes('chat-markdown__table-wrap" dir="ltr"'), 'tables are isolated LTR')
assert(markdownCss.includes('.chat-markdown__block--rtl'), 'RTL paragraphs right-align at content level')
assert(markdownCss.includes('.chat-markdown__block--ltr'), 'LTR paragraphs left-align at content level')

assert(bubble.includes('file-lifecycle-bubble__name'), 'attachments stay in the user turn')
assert(bubble.includes('dir={getMessageTextDirection(name)}'), 'attachment names use content dir')
assert(bubbleCss.includes('file-lifecycle-bubble {'), 'attachment row is compact, not a second column')
assert(/direction:\s*ltr/.test(bubbleCss.match(/\.file-lifecycle \{[\s\S]*?\}/)[0]), 'file status metadata is LTR isolated')

assert(isRtlMarkdown('שלום, מה נשמע?') === true, 'Hebrew paragraph is RTL')
assert(isRtlMarkdown('Hello, how are you?') === false, 'English paragraph is LTR')
assert(isRtlMarkdown('مرحبا كيف حالك') === true, 'Arabic paragraph is RTL')
assert(getMessageTextDirection('Hello שלום') === 'ltr', 'latin-majority mixed string stays LTR')
assert(getMessageTextDirection('שלום חברים Hello') === 'rtl', 'hebrew-majority mixed string is RTL')
assert(getMessageTextDirection('const x = 1') === 'ltr', 'code identifiers are LTR')
assert(
  collectNodeText(['Hello ', { props: { children: 'שלום' } }]) === 'Hello שלום',
  'collectNodeText flattens mixed children'
)

assert(direction.includes('Never used for app chrome'), 'direction helper documents chrome isolation')
assert(
  isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: true }) === true,
  'persistentReady still true when loaded+signed-in'
)
assert(
  isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: false }) === false,
  'persistentReady still false when signed out'
)
assert(
  auth.includes('return Boolean(isLoaded && isSignedIn)'),
  'isClerkPersistentSessionReady formula is unchanged'
)

assert(appCss.includes('@media (max-width: 720px)'), 'narrow screens tighten conversation gutter')
assert(appCss.includes('--conversation-gutter: 1rem'), 'mobile gutter uses the shared token')

console.log('conversation-surface OK')
