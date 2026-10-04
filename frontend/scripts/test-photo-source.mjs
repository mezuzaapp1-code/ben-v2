import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
const dom = new JSDOM('<div id="app"></div>', { url: 'https://ben.test' })
Object.assign(globalThis, { window: dom.window, document: dom.window.document, sessionStorage: dom.window.sessionStorage, localStorage: dom.window.localStorage, IS_REACT_ACT_ENVIRONMENT: true })
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
URL.createObjectURL = () => 'blob:preview'; URL.revokeObjectURL = () => {}
const { default: React, act } = await import('react'), { createRoot } = await import('react-dom/client')
const out = new URL('../.photo-test.mjs', import.meta.url), entry = new URL('../.photo-entry.jsx', import.meta.url)
await writeFile(entry, "export { default } from './src/components/MediaComposer.jsx'; export { submitMedia, withMediaDeadline } from './src/api/mediaSubmission.js';")
if (!process.env.BEN_EDITOR_PREBUILT) {
  const built = await build({ entryPoints: [entry.pathname.replace(/^\/([A-Za-z]:)/, '$1')], define: { 'import.meta.env': '{}' }, bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic', external: ['react', 'react-dom', 'react/jsx-runtime'], loader: { '.css': 'empty' } })
  await writeFile(out, built.outputFiles[0].text)
}
try {
  const { default: Composer, submitMedia, withMediaDeadline } = await import(out.href)
  const root = createRoot(document.getElementById('app'))
  let calls = [], releaseUpload, lost = true
  const file = { name: 'phone.png', size: 3, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }
  const headers = async () => ({ Authorization: 'test-only' })
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.endsWith('/capabilities')) return { ok: true, json: async () => ({ image: true, models: ['image'], video_models: ['video'] }) }
    if (url.includes('/executions?')) return { ok: true, json: async () => ({ executions: [] }) }
    if (url.includes('/photo-sources?')) {
      await new Promise(resolve => { releaseUpload = resolve })
      return { ok: true, json: async () => ({ file_id: 'owned-photo', conversation_id: 'chat' }) }
    }
    if (lost) throw Error('lost reply')
    return { ok: true, json: async () => ({ execution_id: 'execution', status: 'pending' }) }
  }
  const props = { scope: 'account-a', selectedPhoto: file, conversationId: 'chat', buildHeaders: headers, ensureConversation: async () => 'chat', children: React.createElement('div', null, 'Text composer') }
  await act(async () => root.render(React.createElement(Composer, props)))
  assert(document.querySelector('img'))
  assert(!document.body.textContent.includes('Use this photo'))
  assert.equal(calls.filter(c => c.options.method === 'POST').length, 0)
  const input = document.querySelector('textarea[aria-label="Video prompt"]')
  const type = async value => act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  })
  await type('Animate the original photo gently')
  const send = () => document.querySelector('[aria-label="Generate video"]')
  assert(!send().disabled, 'a selected local photo must enable Generate without a separate upload click')
  await act(async () => { send().click(); send().click(); await new Promise(resolve => setTimeout(resolve, 30)) })
  assert(send().disabled)
  assert.match(document.body.textContent, /Uploading your photo/)
  assert.equal(calls.filter(c => c.url.includes('/photo-sources?')).length, 1)
  assert.equal(calls.filter(c => c.url.endsWith('/executions') && c.options.method === 'POST').length, 0)
  await act(async () => { releaseUpload(); await new Promise(resolve => setTimeout(resolve, 20)) })
  assert.match(document.body.textContent, /Submission was not confirmed/)
  assert.equal(input.value, 'Animate the original photo gently')
  const first = JSON.parse(calls.find(c => c.url.endsWith('/executions') && c.options.method === 'POST').options.body)
  assert.equal(first.source_file_id, 'owned-photo'); assert.equal(first.conversation_id, 'chat')
  lost = false
  await type('Different text after uncertain response')
  await act(async () => { send().click(); await new Promise(resolve => setTimeout(resolve, 20)) })
  const posted = calls.filter(c => c.url.endsWith('/executions') && c.options.method === 'POST')
  assert.equal(posted.length, 2)
  assert.deepEqual(JSON.parse(posted[1].options.body), first)
  assert.equal(calls.filter(c => c.url.includes('/photo-sources?')).length, 1, 'recovery must not upload or generate a new intent')
  assert.match(document.body.textContent, /Request received/)
  await act(async () => root.unmount())

  // A stuck auth/conversation step must unlock without submitting later.
  let finishConversation
  const count = calls.length
  await assert.rejects(withMediaDeadline(signal => submitMedia({ scope: 'late', ensureConversation: () => new Promise(resolve => { finishConversation = resolve }), buildHeaders: headers, intent: { prompt: 'late' }, signal, onStage: () => {} }), null, 15), /taking too long/)
  finishConversation('late-chat')
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(calls.length, count, 'late completion after timeout must not submit')
  // Failed upload must not dispatch a paid request; retry reuses the upload key.
  calls = []; let uploadLost = true
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/photo-sources?')) {
      if (uploadLost) throw Error('connection lost')
      return { ok: true, json: async () => ({ file_id: 'recovered-photo' }) }
    }
    return { ok: true, json: async () => ({ execution_id: 'done' }) }
  }
  const request = () => withMediaDeadline(signal => submitMedia({ scope: 'recovery', ensureConversation: async () => 'chat-b', buildHeaders: headers, intent: { prompt: 'animate', model: 'video' }, file, signal, onStage: () => {} }))
  await assert.rejects(request())
  assert.equal(calls.length, 1)
  uploadLost = false; await request()
  assert.equal(calls[0].url, calls[1].url)
  assert.equal(calls.length, 3)
  // Even a transport that delivers a response after abort must retain the paid intent.
  let lateReply
  calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return new Promise(resolve => { lateReply = () => resolve({ ok: true, json: async () => ({ execution_id: 'late-result' }) }) })
  }
  await assert.rejects(withMediaDeadline(signal => submitMedia({ scope: 'late-paid', ensureConversation: async () => 'chat', buildHeaders: headers, intent: { prompt: 'original', model: 'video' }, signal, onStage: () => {} }), null, 15))
  const pendingKey = 'ben-media-pending-v1:late-paid:chat'
  const preserved = sessionStorage.getItem(pendingKey)
  assert(preserved)
  lateReply(); await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(sessionStorage.getItem(pendingKey), preserved)
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ execution_id: 'late-result' }) } }
  await withMediaDeadline(signal => submitMedia({ scope: 'late-paid', ensureConversation: async () => 'chat', buildHeaders: headers, intent: { prompt: 'different' }, signal, onStage: () => {} }))
  assert.equal(calls[0].options.body, calls[1].options.body)
  console.log('PASS: single-click photo → upload → generation; immediate progress; duplicate-click guard; exact paid-intent recovery; photo retry identity; bounded waits and no late submission; selection makes no POST.')
} finally { await unlink(out).catch(() => {}); await unlink(entry).catch(() => {}) }
