import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Replace only Vite's environment import, retaining the actual API implementation.
const source = readFileSync(new URL('../src/api/media.js', import.meta.url), 'utf8')
  .replace("import { BEN_API_BASE } from '../config.js'", "const BEN_API_BASE = 'https://ben.example'")
const { pendingMedia, clearPendingMedia, mediaRequest, mediaTerminal } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const map = new Map()
const storage = { getItem: key => map.get(key), setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) }
const intent = { conversation_id: 'conversation', model: 'gemini-3.1-flash-image', prompt: 'chair' }
const first = pendingMedia(storage, 'org:user:conversation', intent)
assert.deepEqual(pendingMedia(storage, 'org:user:conversation', { ...intent, prompt: 'changed after timeout' }), first)
assert.notEqual(pendingMedia(storage, 'org:other:conversation', intent).idempotency_key, first.idempotency_key)
clearPendingMedia(storage, 'org:user:conversation')
assert.notEqual(pendingMedia(storage, 'org:user:conversation', intent).idempotency_key, first.idempotency_key)
assert.throws(() => pendingMedia({ getItem() { throw new Error('storage denied') } }, 'scope', intent))
let captured
globalThis.fetch = async (url, options) => {
  captured = { url, options }
  return { ok: true, json: async () => ({ execution_id: 'ben-owned' }), blob: async () => new Blob(['bytes']) }
}
assert.deepEqual(await mediaRequest('/executions', { Authorization: 'Bearer test-only' }, { body: first }), { execution_id: 'ben-owned' })
assert.equal(captured.url, 'https://ben.example/api/media/executions')
assert.equal(captured.options.method, 'POST')
assert.equal(captured.options.headers.Authorization, 'Bearer test-only')
assert.equal(JSON.parse(captured.options.body).idempotency_key, first.idempotency_key)
await mediaRequest('/executions/ben-owned', {})
assert.equal(captured.options.method, 'GET')
assert.equal(captured.options.body, undefined)
await mediaRequest('/resources/ben-owned/content', {}, { binary: true })
assert.equal(captured.options.cache, 'no-store')
globalThis.fetch = async () => ({ ok: false, status: 503 })
await assert.rejects(mediaRequest('/executions', {}), error => error.status === 503)
assert.equal(mediaTerminal('submission_unknown'), false)
assert.equal(mediaTerminal('succeeded'), true)
assert.equal(mediaTerminal('failed'), true)
assert.equal(mediaTerminal('expired'), true)
console.log('Media client checks PASS: stable retry identity, principal scope, storage failure, separate media dispatch, authenticated bytes, read-only polling, status handling.')
