import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'

const output = new URL('../.narration-test.mjs', import.meta.url)
const built = await build({ entryPoints: [new URL('../src/components/NarrationPanel.jsx', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': JSON.stringify({ DEV: true }) } })
await writeFile(output, built.outputFiles[0].text)
try {
  const { default: Panel } = await import(output.href)
  const dom = new JSDOM('<div id="app"></div>', { url: 'https://ben.test' })
  Object.assign(globalThis, { window: dom.window, document: dom.window.document,
    sessionStorage: dom.window.sessionStorage, IS_REACT_ACT_ENVIRONMENT: true })
  let root, accepted = 0, fail = true, submitted = []
  const files = [{ id: 'voice', original_filename: 'voice.wav', media_type: 'audio/wav', status: 'uploaded' },
    { id: 'music', original_filename: 'music.wav', media_type: 'audio/wav', status: 'ready' }]
  globalThis.fetch = async (url, options) => {
    assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer test-only')
    if (url.includes('/files')) return { ok: true, json: async () => ({ items: files }) }
    assert.ok(url.endsWith('/narration-replacements'))
    submitted.push(JSON.parse(options.body))
    if (fail) throw new Error('lost response')
    return { ok: true, json: async () => ({ execution_id: 'execution' }) }
  }
  const props = { workspaceId: 'workspace', scope: 'org:user:conversation', rows: [],
    buildHeaders: async () => ({ Authorization: 'Bearer test-only' }),
    ensureConversation: async () => 'conversation', onAccepted: () => accepted++ }
  const mount = async p => {
    root = createRoot(document.getElementById('app'))
    await act(async () => root.render(React.createElement(Panel, p)))
  }
  await mount({ ...props, workspaceId: null })
  assert.match(document.body.textContent, /Open a project workspace/)
  assert.equal(document.querySelector('button').disabled, true)
  await act(async () => root.unmount())
  await mount(props)
  assert.match(document.body.textContent, /No ready BEN video/)
  assert.equal(document.querySelector('button').disabled, true)
  await act(async () => root.render(React.createElement(Panel, { ...props,
    rows: [{ resource_id: 'video', status: 'succeeded', mime_type: 'video/mp4', model: 'Local', created_at: 'today' }] })))
  const selects = document.querySelectorAll('select')
  for (const [index, value] of ['video', 'voice', 'music'].entries()) {
    await act(async () => { selects[index].value = value; selects[index].dispatchEvent(new dom.window.Event('change', { bubbles: true })) })
  }
  assert.equal(document.querySelector('button').disabled, false)
  await act(async () => document.querySelector('button').click())
  assert.match(document.body.textContent, /Retry saved request/)
  assert.ok([...document.querySelectorAll('select')].every(s => s.disabled))
  assert.equal(submitted.length, 1)
  await act(async () => root.unmount())
  await mount(props)
  assert.match(document.body.textContent, /Retry saved request/)
  fail = false
  await act(async () => document.querySelector('button').click())
  assert.deepEqual(submitted[1], submitted[0])
  assert.deepEqual(Object.keys(submitted[0]).sort(), ['conversation_id', 'workspace_id', 'video_resource_id', 'music_file_id', 'narration_file_id', 'idempotency_key'].sort())
  assert.equal(accepted, 1)
  assert.equal(sessionStorage.length, 0)
  await act(async () => root.unmount())
  console.log('Narration UI passed: missing workspace/video, authenticated dispatch, frozen retry across remount, original asset IDs, accepted callback.')
} finally { await unlink(output) }
