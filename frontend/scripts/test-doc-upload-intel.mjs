/**
 * Document Upload Intelligence V1 — overview merge + PR #46 poller contract.
 * Run: node frontend/scripts/test-doc-upload-intel.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  FILE_INITIAL_READ_EVENT,
  isAwaitingInitialRead,
  isNonTerminalFile,
  mergeInitialReadIntoMessages,
} from '../src/lib/fileStatus.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const chatId = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
const fileA = 'a0000000-0000-0000-0000-000000000001'

assert(
  isAwaitingInitialRead({
    status: 'ready',
    source_chat_id: chatId,
    initial_read_status: 'none',
  }) === true,
  'READY + none still awaits Initial Read so the poller does not stop early'
)
assert(
  isAwaitingInitialRead({
    status: 'ready',
    source_chat_id: chatId,
    initial_read_status: 'pending',
  }) === true,
  'READY + pending awaits Initial Read'
)
assert(
  isAwaitingInitialRead({
    status: 'ready',
    source_chat_id: chatId,
    initial_read_status: 'complete',
  }) === false,
  'complete is terminal for Initial Read'
)
assert(
  isAwaitingInitialRead({
    status: 'ready',
    source_chat_id: chatId,
    initial_read_status: 'failed',
  }) === false,
  'failed Initial Read must stop the poller'
)
assert(
  isAwaitingInitialRead({
    status: 'ready',
    source_chat_id: chatId,
    initial_read_status: 'skipped',
  }) === false,
  'skipped Initial Read must stop the poller'
)
assert(
  isNonTerminalFile({
    status: 'ready',
    source_chat_id: chatId,
    initial_read_status: 'failed',
  }) === false,
  'failed Initial Read is terminal for inventory polling'
)
assert(
  isAwaitingInitialRead({
    status: 'ready',
    source_chat_id: '',
    initial_read_status: 'none',
  }) === false,
  'File Library uploads without source_chat_id are not awaited'
)
assert(
  isNonTerminalFile({
    status: 'ready',
    source_chat_id: chatId,
    initial_read_status: 'none',
  }) === true,
  'inventory keeps polling through READY until Initial Read finishes'
)
assert(
  isNonTerminalFile({ status: 'ready', job_status: 'running' }) === false,
  'ready without chat source stays terminal (PR #46 honesty)'
)

const lifecycle = {
  kind: 'file_upload',
  file_id: fileA,
  filename: 'A.pdf',
}
const overview = {
  role: 'assistant',
  kind: 'chat',
  source_event: FILE_INITIAL_READ_EVENT,
  source_file_id: fileA,
  content: 'Grounded overview of A.',
  used_files: [{ id: fileA, name: 'A.pdf' }],
}
const merged = mergeInitialReadIntoMessages([lifecycle, { role: 'user', content: 'hi' }], [overview])
assert(merged[0] === lifecycle, 'lifecycle row stays first')
assert(merged[1] === overview, 'overview is inserted immediately after the lifecycle row')
assert(merged[2].content === 'hi', 'later conversation rows are preserved')

const again = mergeInitialReadIntoMessages(merged, [overview])
assert(again === merged, 'duplicate overview from a second wake is not inserted')

const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
assert(app.includes('mergeInitialReadIntoMessages'), 'App merges Initial Read into the thread')
assert(app.includes('isAwaitingInitialRead'), 'App fetches while Initial Read is in flight')
assert(!app.includes("kind: 'file_overview'"), 'App does not introduce kind=file_overview')
assert(app.includes('FileLifecycleBubble'), 'PR #46 lifecycle bubble remains')
assert(app.includes("kind: 'file_upload'"), 'PR #46 file_upload row remains')

const fileStatus = readFileSync(join(root, 'src/lib/fileStatus.js'), 'utf8')
assert(fileStatus.includes("kind === 'file_upload'"), 'patch helper still targets file_upload')
assert(!fileStatus.includes("kind: 'file_overview'"), 'fileStatus does not add file_overview')

console.log('OK: document upload intelligence V1 frontend contract')
