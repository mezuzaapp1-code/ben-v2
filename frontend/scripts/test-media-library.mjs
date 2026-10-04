import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink, readFile } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
const dom = new JSDOM('<html lang="en"><body><div id="app"></div></body></html>', { url: 'https://ben.test' })
Object.assign(globalThis, { window: dom.window, document: dom.window.document, MutationObserver: dom.window.MutationObserver, localStorage: dom.window.localStorage, IS_REACT_ACT_ENVIRONMENT: true })
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
dom.window.HTMLMediaElement.prototype.pause = () => {}
URL.createObjectURL = () => 'blob:test'
URL.revokeObjectURL = () => {}
const { default: React, act } = await import('react'), { createRoot } = await import('react-dom/client')
const output = new URL('../.media-library-test.mjs', import.meta.url)
if (!process.env.BEN_EDITOR_PREBUILT) {
  const result = await build({ entryPoints: [new URL('../src/components/MediaLibrary.jsx', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')], bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic', external: ['react', 'react-dom', 'react/jsx-runtime'], loader: { '.css': 'empty' }, define: { 'import.meta.env': '{}' } })
  await writeFile(output, result.outputFiles[0].text)
}
try {
  const { default: Library, MediaLibraryNav: Nav } = await import(output.href)
  const resource = '00000000-0000-4000-8000-000000000001'
  let rows = [], fail = false, allowed = true, failContent = false, calls = [], view = null
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.endsWith('/capabilities')) return { ok: true, json: async () => ({ edit_documents: allowed }) }
    if (url.endsWith('/content')) return { ok: !failContent, status: 404, blob: async () => new Blob(['video']) }
    if (url.includes('/executions?')) return { ok: true, json: async () => ({ executions: [{ status: 'succeeded', resource_id: resource, mime_type: 'video/mp4', created_at: '2026-10-04', execution_id: 'one' }, { status: 'failed', resource_id: resource, mime_type: 'video/mp4', execution_id: 'failed' }] }) }
    return { ok: !fail, status: 503, json: async () => ({ documents: rows }) }
  }
  const headers = async () => ({ Authorization: 'test-account' })
  const root = createRoot(document.getElementById('app'))
  const render = async (scope = 'a') => act(async () => root.render(React.createElement(React.Fragment, null,
    React.createElement(Nav, { onOpen: next => { view = next; void render() } }),
    view && React.createElement(Library, { key: scope, scope, initialView: view, conversationId: 'chat', buildHeaders: headers, onClose: () => { view = null; void render() } }))))
  const button = text => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  const click = async b => act(async () => b.click())
  await render()
  await click(button('▣ My saved work'))
  assert.match(document.body.textContent, /No saved video edits yet/)
  assert.equal(document.activeElement.getAttribute('aria-label'), 'Close media studio')
  await click(button('Videos in this chat'))
  assert.equal(document.querySelectorAll('.ben-media-library__card').length, 1)
  await click(document.querySelector('.ben-media-library__card'))
  assert(document.querySelector('[aria-label="Video subtitle editor"]'))
  assert(document.querySelector('.ben-media-library').hasAttribute('inert'))
  // Escape belongs to the editor and must never close the library behind it.
  window.confirm = () => false
  await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  assert(document.querySelector('[aria-label="Media studio"]'))
  assert(document.querySelector('[aria-label="Video subtitle editor"]'))
  window.confirm = () => true
  await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  assert(!document.querySelector('[aria-label="Video subtitle editor"]'))
  assert(document.querySelector('[aria-label="Media studio"]'))
  failContent = true
  await click(document.querySelector('.ben-media-library__card'))
  assert.match(document.body.textContent, /Video unavailable/)
  await click(button('Back to my work'))
  fail = true
  await click(button('My saved work'))
  assert.match(document.body.textContent, /Could not load your work/)
  fail = false
  rows = [{ document_id: 'doc', resource_id: resource, head_number: 3, updated_at: '2026-10-04' }]
  await click(button('Try again'))
  assert.match(document.body.textContent, /Version 3/)
  allowed = false
  await render('different-account')
  assert.match(document.body.textContent, /not enabled for this account/)
  assert(!document.body.textContent.includes('Version 3'))
  assert(calls.every(call => call.options.method === 'GET'))
  assert(calls.every(call => call.options.headers.Authorization === 'test-account'))
  await act(async () => root.unmount())
  assert.equal(document.body.style.overflow, '')
  const app = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(app, /<MediaLibraryNav/)
  assert.match(app, /key=\{`\$\{sessionTenantId\}:\$\{userId\}:\$\{mediaLibraryView\}`\}/)
  console.log('PASS: main navigation, private list, empty/error/retry, completed video entry, unsaved Escape guard, failed media recovery, account isolation; no paid mutations.')
} finally { await unlink(output).catch(() => {}) }
