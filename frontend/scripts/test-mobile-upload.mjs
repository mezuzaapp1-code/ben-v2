import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'

const output = new URL('../.mobile-test.mjs', import.meta.url)
const built = await build({ entryPoints: [new URL('../src/components/MobileVideoUpload.jsx', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': JSON.stringify({ DEV: true }) } })
await writeFile(output, built.outputFiles[0].text)
try {
  const { default: Panel } = await import(output.href)
  const dom = new JSDOM('<div id="app"></div>', { url: 'https://ben.test' })
  Object.assign(globalThis, { window: dom.window, document: dom.window.document,
    sessionStorage: dom.window.sessionStorage, IS_REACT_ACT_ENVIRONMENT: true })
  let root, accepted = 0, fail = true
  const calls = []
  globalThis.fetch = async (url, options) => {
    assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer test-only')
    assert.equal(new Headers(options.headers).get('Content-Type'), 'application/octet-stream')
    calls.push({ url, body: options.body })
    if (fail) throw new Error('lost response')
    return { ok: true }
  }
  const props = { workspaceId: 'workspace', scope: 'org:user', buildHeaders: async () => ({ Authorization: 'Bearer test-only' }),
    ensureConversation: async () => 'conversation', onAccepted: () => accepted++ }
  async function mount(p) {
    root = createRoot(document.getElementById('app'))
    await act(async () => root.render(React.createElement(Panel, p)))
  }
  async function choose(file) {
    const input = document.querySelector('input')
    Object.defineProperty(input, 'files', { configurable: true, value: [file] })
    await act(async () => input.dispatchEvent(new dom.window.Event('change', { bubbles: true })))
  }
  const file = new File(['test-only'], 'phone.mp4', { type: 'video/mp4' })
  await mount({ ...props, workspaceId: null })
  assert.equal(document.querySelector('input').disabled, true)
  await act(async () => root.unmount())
  await mount(props)
  await choose(file)
  await act(async () => document.querySelector('button').click())
  // crypto digest completes asynchronously outside the click's return value.
  for (let i = 0; i < 100 && calls.length < 1; i++) await act(async () => new Promise(r => setTimeout(r, 10)))
  assert.equal(calls.length, 1)
  await act(async () => root.unmount())
  await mount(props)
  await choose(new File(['different'], 'other.mp4'))
  await act(async () => { document.querySelector('button').click(); await new Promise(r => setTimeout(r, 50)) })
  assert.equal(calls.length, 1)
  assert.match(document.body.textContent, /same video/)
  await choose(file)
  fail = false
  await act(async () => { document.querySelector('button').click(); await new Promise(r => setTimeout(r, 50)) })
  assert.equal(calls.length, 2)
  assert.equal(calls[0].url, calls[1].url)
  assert.equal(accepted, 1)
  assert.equal(sessionStorage.length, 0)
  await act(async () => root.unmount())
  console.log('Mobile upload UI: workspace gate, authenticated bytes, retry across remount, source mismatch rejection, accepted refresh.')
} finally { await unlink(output) }
