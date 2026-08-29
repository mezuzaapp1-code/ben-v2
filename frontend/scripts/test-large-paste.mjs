/**
 * Large Paste V1 — composer parts, threshold, order, Focus isolation.
 * Run: node frontend/scripts/test-large-paste.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { buildAttentionQuery } from '../src/lib/attentionQuery.js'
import {
  LARGE_PASTE_PROVIDER_MAX_CHARS,
  LARGE_PASTE_THRESHOLD,
  LARGE_PASTE_UNWRAP_CEILING,
  appendFileRefPart,
  canSendComposerParts,
  composerPartsFromMessage,
  decodeUserTurnContent,
  displayTextFromParts,
  encodeUserTurn,
  expandPartsForProvider,
  focusSourceFromParts,
  formatLargePasteStub,
  formatPasteChipLabel,
  hasFileRef,
  insertLargePasteAtCursor,
  instructionTextFromParts,
  isVisionImageFile,
  providerExpansionError,
  publicConversationTitle,
  shouldCreateLargePaste,
  unwrapLargePaste,
  visibleUserTurnText,
} from '../src/lib/largePaste.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

assert(LARGE_PASTE_THRESHOLD === 10000, 'threshold 10000 code points')
assert(LARGE_PASTE_UNWRAP_CEILING === 25000, 'unwrap ceiling 25000')
assert(LARGE_PASTE_PROVIDER_MAX_CHARS === 400000, 'provider refuse 400000')

{
  const inline = 'x'.repeat(9999)
  const large = 'y'.repeat(10000)
  assert(shouldCreateLargePaste(inline) === false, '9999 stays inline')
  assert(shouldCreateLargePaste(large) === true, '10000 becomes Large Paste')
}

{
  const body = 'Z'.repeat(80000)
  const next = insertLargePasteAtCursor([{ type: 'text', text: 'Hello ' }], 0, 6, 6, body)
  const texts = next.filter((part) => part.type === 'text').map((part) => part.text)
  assert(texts.every((text) => !text.includes(body)), '80k does not enter textarea parts')
  assert(next.some((part) => part.type === 'large_paste' && part.text === body), '80k stored on paste part')
  assert(formatPasteChipLabel(next.find((part) => part.type === 'large_paste')).includes('80,000'), 'chip shows count')
}

{
  const paste = 'P'.repeat(12000)
  const parts = [
    { type: 'text', text: 'Before.\n' },
    { type: 'large_paste', id: 'p1', label: 'Pasted text', text: paste, char_count: 12000 },
    { type: 'text', text: '\nAfter.' },
  ]
  const encoded = encodeUserTurn(parts)
  const payload = JSON.parse(encoded)
  assert(payload.ben === 1 && payload.kind === 'user_turn', 'envelope kind')
  assert(payload.parts.map((part) => part.type).join(',') === 'text,large_paste,text', 'order text/paste/text')
  assert(expandPartsForProvider(payload.parts) === `Before.\n${paste}\nAfter.`, 'expand preserves order')
  const decoded = decodeUserTurnContent(encoded)
  assert(!decoded.content.includes(paste), 'display is not the wall')
  assert(decoded.content.includes(formatLargePasteStub(12000)), 'display uses stub')
  assert(!decoded.content.includes('{"ben":'), 'display never leaks JSON')
}

{
  const p1 = 'ONE' + 'א'.repeat(10000)
  const p2 = 'TWO' + 'ב'.repeat(10000)
  const parts = insertLargePasteAtCursor(
    insertLargePasteAtCursor([{ type: 'text', text: ' mid  end' }], 0, 5, 5, p2),
    0,
    0,
    0,
    p1
  )
  const encoded = encodeUserTurn(parts)
  assert(expandPartsForProvider(JSON.parse(encoded).parts) === `${p1} mid ${p2} end`, 'two pastes keep order')
}

{
  const pasteOnly = insertLargePasteAtCursor([{ type: 'text', text: '' }], 0, 0, 0, 'Q'.repeat(15000))
  assert(canSendComposerParts(pasteOnly), 'paste-only is sendable')
  assert(encodeUserTurn(pasteOnly).includes('"kind":"user_turn"'), 'paste-only encodes envelope')
}

{
  const hebrew = 'שלום 🌍\n```md\n# Title\n```\n' + 'מדריך '.repeat(3000)
  const parts = [
    { type: 'text', text: 'בדוק:\n' },
    { type: 'large_paste', id: 'he', label: 'Pasted text', text: hebrew, char_count: [...hebrew].length },
    { type: 'text', text: '\nתודה' },
  ]
  const encoded = encodeUserTurn(parts)
  const decoded = decodeUserTurnContent(encoded)
  assert(decoded.parts[1].text === hebrew, 'hebrew/emoji/markdown exact')
  assert(expandPartsForProvider(decoded.parts) === `בדוק:\n${hebrew}\nתודה`, 'unicode expand exact')
}

{
  const body = 'W'.repeat(200000)
  const encoded = encodeUserTurn([
    { type: 'text', text: 'Note:\n' },
    { type: 'large_paste', id: 'w', label: 'Pasted text', text: body, char_count: 200000 },
  ])
  assert(decodeUserTurnContent(encoded).parts[1].text === body, '200k exact')
  assert(providerExpansionError(expandPartsForProvider(decodeUserTurnContent(encoded).parts)) === null, '200k sendable')
}

{
  const body = 'M'.repeat(1000000)
  const err = providerExpansionError(body)
  assert(err && err.includes('1,000,000'), '1MB-class explicit')
  assert(err.includes('not truncated'), '1MB not silently trimmed')
  assert(err.includes('in one request'), '400k is a BEN request/transport guard')
  assert(err.includes('transport limit'), 'wording names transport, not model fit')
  assert(!err.includes('to the model'), 'does not claim the model can fit it')
}

{
  const paste = 'F'.repeat(40000)
  const parts = [
    { type: 'text', text: 'What is the opening width?' },
    { type: 'large_paste', id: 'f', label: 'Pasted text', text: paste, char_count: 40000 },
  ]
  const focus = focusSourceFromParts(parts)
  assert(focus === 'What is the opening width?', 'Focus uses instruction')
  assert(!focus.includes(paste), 'Focus does not receive full paste')
  const bounded = buildAttentionQuery(focus)
  assert(bounded === focus, 'instruction Focus stays short')
}

{
  const pasteOnly = [
    { type: 'large_paste', id: 'po', label: 'Pasted text', text: 'G'.repeat(50000), char_count: 50000 },
  ]
  const source = focusSourceFromParts(pasteOnly)
  assert(source === formatLargePasteStub(50000), 'paste-only Focus uses stub')
  assert(buildAttentionQuery(source).length < 200, 'paste-only Focus stays bounded')
}

{
  const small = 'unwrap-me-' + 'x'.repeat(100)
  const parts = insertLargePasteAtCursor([{ type: 'text', text: 'A  B' }], 0, 2, 2, small)
  const unwrapped = unwrapLargePaste(parts, 1)
  assert(unwrapped.ok, 'unwrap under ceiling')
  assert(expandPartsForProvider(unwrapped.parts) === `A ${small} B`, 'unwrap restores exact body at position')
  assert(!unwrapped.parts.some((part) => part.type === 'large_paste'), 'unwrap removes chip')
}

{
  const huge = 'H'.repeat(25001)
  const parts = insertLargePasteAtCursor([{ type: 'text', text: '' }], 0, 0, 0, huge)
  const denied = unwrapLargePaste(parts, 1)
  assert(denied.ok === false, 'unwrap above ceiling refused')
  assert(denied.parts.some((part) => part.type === 'large_paste' && part.text === huge), 'no silent truncate')
}

{
  const encoded = encodeUserTurn([
    { type: 'text', text: 'Hi ' },
    { type: 'large_paste', id: 'r', label: 'Pasted text', text: 'R'.repeat(11000), char_count: 11000 },
  ])
  const restored = composerPartsFromMessage(decodeUserTurnContent(encoded))
  assert(restored.some((part) => part.type === 'large_paste'), 'reload reconstructs chip')
  assert(displayTextFromParts(restored).includes(formatLargePasteStub(11000)), 'reload display is stub')
}

{
  assert(decodeUserTurnContent('legacy raw').content === 'legacy raw', 'legacy raw unchanged')
  assert(decodeUserTurnContent('{"ben":1,"kind":"chat","text":"x"}').content.includes('{"ben":'), 'non user_turn stays raw')
}

{
  const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
  const composer = readFileSync(join(root, 'src/components/ComposerCapsule.jsx'), 'utf8')
  const largePaste = readFileSync(join(root, 'src/lib/largePaste.js'), 'utf8')
  assert(app.includes('encodeUserTurn'), 'send encodes structured turn')
  assert(app.includes('message: encoded'), 'chat stream receives encoded/expanded current turn')
  assert(app.includes('focusSourceFromParts'), 'Focus uses instruction/stub source')
  assert(app.includes('setComposerParts(snapshot)'), 'failed send restores composer parts')
  assert(app.includes('Show in text field') || composer.includes('Show in text field'), 'unwrap UX present')
  assert(composer.includes('shouldCreateLargePaste'), 'composer handles paste events')
  assert(!largePaste.includes('workspace'), 'large paste lib does not mention workspace')
  assert(!composer.includes('handleWorkspaceFileAttach'), 'paste path is not Attach File')
  assert(app.includes('handleWorkspaceFileAttach'), 'explicit upload path remains')
  assert(app.includes('appendFileRefPart'), 'image upload adds current-turn file_ref')
  assert(app.includes('hasStructuredUserTurn'), 'send keeps file_ref parts')
  assert(composer.includes('file_ref'), 'composer renders image chip')
}

{
  assert(isVisionImageFile({ type: 'image/png', name: 'a.png' }) === true, 'png is vision')
  assert(isVisionImageFile({ type: 'text/plain', name: 'note.txt' }) === false, 'txt is not vision')
  const parts = appendFileRefPart([{ type: 'text', text: 'מה הצבע?' }], {
    file_id: '33333333-3333-4333-8333-333333333333',
    name: 'square.png',
  })
  assert(canSendComposerParts(parts) === true, 'text+image can send')
  assert(
    canSendComposerParts(appendFileRefPart([{ type: 'text', text: '' }], {
      file_id: '33333333-3333-4333-8333-333333333333',
      name: 'square.png',
    })) === true,
    'image-only can send',
  )
  const encoded = encodeUserTurn(parts)
  const payload = JSON.parse(encoded)
  assert(payload.kind === 'user_turn', 'file_ref uses envelope')
  assert(payload.parts.some((part) => part.type === 'file_ref'), 'envelope has file_ref')
  assert(!JSON.stringify(payload).includes('storage_key'), 'no storage_key in envelope')
  assert(hasFileRef(parts) === true, 'hasFileRef')
}

{
  const paste = 'BODY'.repeat(4000)
  const message = {
    role: 'user',
    content: `Note.\n${formatLargePasteStub(paste.length)}`,
    parts: [
      { type: 'text', text: 'Note.\n' },
      { type: 'large_paste', id: 'p', label: 'Pasted text', text: paste, char_count: paste.length },
    ],
  }
  const visible = visibleUserTurnText(message)
  assert(visible === `Note.\n${paste}`, 'sent turn shows instruction plus paste body')
  assert(!visible.includes('Large paste'), 'sent turn has no stub header')
  assert(!visible.includes('Pasted text ·'), 'sent turn has no paste chip')
  assert(displayTextFromParts(message.parts).includes(formatLargePasteStub(paste.length)), 'internal stub unchanged')
  assert(expandPartsForProvider(message.parts) === `Note.\n${paste}`, 'provider expansion unchanged')
}

{
  const paste = 'ONLY'.repeat(3000)
  const message = {
    role: 'user',
    content: formatLargePasteStub(paste.length),
    parts: [{ type: 'large_paste', id: 'only', text: paste, char_count: paste.length }],
  }
  assert(visibleUserTurnText(message) === paste, 'paste-only sent turn is the body')
}

{
  const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
  assert(app.includes('visibleUserTurnText(m)'), 'conversation uses visible user-turn text')
  assert(app.includes('content={m.role === \'user\' ? visibleUserTurnText(m) : m.content}'), 'Copy uses visible user-turn text')
  assert(!app.includes('bubble-large-paste'), 'App no longer renders a sent paste chip')
  assert(
    app.includes("instructionTextFromParts(snapshot).trim().slice(0, 48) || 'Conversation'"),
    'new thread titles come from instruction text',
  )
  assert(!/title:\s*display\.slice/.test(app), 'new thread titles are not derived from display stubs')
}

{
  const paste = 'BODY'.repeat(4000)
  const pasteOnly = [{ type: 'large_paste', id: 'only', text: paste, char_count: paste.length }]
  const withInstruction = [
    { type: 'text', text: 'Summarize the contract risks' },
    { type: 'large_paste', id: 'p', text: paste, char_count: paste.length },
  ]
  const pasteOnlyTitle = instructionTextFromParts(pasteOnly).trim().slice(0, 48) || 'Conversation'
  const instructionTitle = instructionTextFromParts(withInstruction).trim().slice(0, 48) || 'Conversation'
  assert(pasteOnlyTitle === 'Conversation', 'paste-only turn titles Conversation')
  assert(instructionTitle === 'Summarize the contract risks', 'instruction + paste titles from instruction')
  assert(!instructionTitle.includes('Large paste'), 'new title never uses the stub')
  assert(publicConversationTitle(formatLargePasteStub(17292)) === 'Conversation', 'historical stub title is sanitized')
  assert(
    publicConversationTitle(`Review this.${formatLargePasteStub(17292)}`) === 'Review this.',
    'historical mixed title keeps instruction',
  )
  assert(publicConversationTitle('Normal question about retrieval') === 'Normal question about retrieval', 'normal title unchanged')
  assert(publicConversationTitle('  ') === 'Conversation', 'blank title falls back')
  assert(publicConversationTitle(`[Large paste · 17,292 charact`) === 'Conversation', 'sliced historical stub is sanitized')
  assert(publicConversationTitle('[Large pas') === 'Conversation', 'mid-token sliced stub is sanitized')
  assert(
    publicConversationTitle(`${'Please review this contract carefully'}${formatLargePasteStub(17292)}`.slice(0, 48)) ===
      'Please review this contract carefully',
    'sliced instruction+stub title keeps instruction only',
  )
}

{
  const header = readFileSync(join(root, 'src/components/ChatHeader.jsx'), 'utf8')
  const nav = readFileSync(join(root, 'src/components/NavDrawer.jsx'), 'utf8')
  const headerCss = readFileSync(join(root, 'src/components/ChatHeader.css'), 'utf8')
  assert(header.includes('publicConversationTitle(title)'), 'header sanitizes conversation title')
  assert(nav.includes('publicConversationTitle(t.title)'), 'history sanitizes thread titles')
  assert(/position:\s*sticky/.test(headerCss), 'chat-header sticky behavior unchanged')
  assert(header.includes('className="chat-header"'), 'header chrome class unchanged')
}

console.log('PASS large paste v1 composer + encode/decode + focus isolation')
