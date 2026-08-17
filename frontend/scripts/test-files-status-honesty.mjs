/**
 * Gate 1 — Files status honesty + used-files (no Vitest/RTL).
 * Run: node frontend/scripts/test-files-status-honesty.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  FILE_STATUS_LABELS,
  createBoundedStatusPoller,
  fileStatusLabel,
  isNonTerminalFileStatus,
  isTerminalFileStatus,
  sanitizeUsedFiles,
  unavailableChatNote,
  usedFilesFromDoneEvent,
  isStandardChatAssistant,
} from '../src/lib/fileStatus.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// 1. queued displays as not available to chat
assert(
  fileStatusLabel('queued') === 'Queued — not available to chat yet',
  'queued label'
)
assert(fileStatusLabel('uploaded') === FILE_STATUS_LABELS.queued, 'uploaded maps to queued copy')
assert(isNonTerminalFileStatus('queued') === true, 'queued is non-terminal')

// 2. processing displays as not available to chat
assert(
  fileStatusLabel('processing') === 'Processing — not available to chat yet',
  'processing label'
)
assert(isNonTerminalFileStatus('processing') === true, 'processing is non-terminal')

// 3. ready displays as available
assert(fileStatusLabel('ready') === 'Ready — available to chat', 'ready label')
assert(isTerminalFileStatus('ready') === true, 'ready is terminal')
assert(isNonTerminalFileStatus('ready') === false, 'ready is not non-terminal')

// 4. failed displays as failed
assert(fileStatusLabel('failed') === 'Failed', 'failed label')
assert(isTerminalFileStatus('failed') === true, 'failed is terminal')

const overlay = readFileSync(join(root, 'src/components/FileLibraryOverlay.jsx'), 'utf8')
const sidebar = readFileSync(join(root, 'src/components/KnowledgeSidebar.jsx'), 'utf8')
const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')

assert(overlay.includes('fileStatusLabel(item.status)'), 'library uses honesty labels')
assert(sidebar.includes('fileStatusLabel(file.status)'), 'sidebar uses honesty labels')
assert(app.includes('fileStatusLabel(status)'), 'composer uses honesty labels')
assert(!app.includes("result?.status || 'ready'"), 'composer does not invent READY')
assert(app.includes("result?.status || 'uploaded'"), 'composer falls back to uploaded, not ready')
assert(overlay.includes('createBoundedStatusPoller'), 'library polls via bounded poller')
assert(sidebar.includes('createBoundedStatusPoller'), 'sidebar polls via bounded poller')
assert(overlay.includes('poller.stop()'), 'library stops poller on cleanup')
assert(sidebar.includes('poller.stop()'), 'sidebar stops poller on cleanup')
assert(overlay.includes('hasNonTerminal'), 'library polls only while non-terminal')
assert(sidebar.includes('hasNonTerminal'), 'sidebar polls only while non-terminal')

// 5–7. polling occurs only while non-terminal, stops at ready/failed, cleanup on unmount
{
  const files = [{ status: 'queued' }]
  let calls = 0
  const poller = createBoundedStatusPoller({
    shouldPoll: () => files.some((f) => isNonTerminalFileStatus(f.status)),
    refresh: async () => {
      calls += 1
      if (calls >= 2) files[0].status = 'ready'
    },
    intervalMs: 8,
  })
  poller.start()
  await sleep(40)
  assert(calls >= 1, 'poller refreshes while queued')
  await sleep(30)
  assert(calls === 2, `poller stops after ready (calls=${calls})`)
  assert(poller.stopped === true, 'poller marked stopped at ready')
  const afterReady = calls
  await sleep(20)
  assert(calls === afterReady, 'no further polls after terminal ready')
}

{
  const files = [{ status: 'processing' }]
  let calls = 0
  const poller = createBoundedStatusPoller({
    shouldPoll: () => files.some((f) => isNonTerminalFileStatus(f.status)),
    refresh: async () => {
      calls += 1
      files[0].status = 'failed'
    },
    intervalMs: 8,
  })
  poller.start()
  await sleep(25)
  assert(calls === 1, 'one refresh while processing')
  assert(poller.stopped === true, 'poller stops at failed')
  await sleep(20)
  assert(calls === 1, 'no further polls after failed')
}

{
  let calls = 0
  const poller = createBoundedStatusPoller({
    shouldPoll: () => true,
    refresh: async () => {
      calls += 1
    },
    intervalMs: 8,
  })
  poller.start()
  await sleep(12)
  poller.stop()
  const afterStop = calls
  await sleep(25)
  assert(calls === afterStop, 'unmount/stop prevents further polls')
  assert(poller.stopped === true, 'stop() is terminal')
}

{
  let calls = 0
  const poller = createBoundedStatusPoller({
    shouldPoll: () => false,
    refresh: async () => {
      calls += 1
    },
    intervalMs: 5,
  })
  poller.start()
  await sleep(20)
  assert(calls === 0, 'no polling when all visible files are terminal')
  poller.stop()
}

{
  let inFlight = 0
  let maxInFlight = 0
  let calls = 0
  const poller = createBoundedStatusPoller({
    shouldPoll: () => calls < 2,
    refresh: async () => {
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      calls += 1
      await sleep(20)
      inFlight -= 1
    },
    intervalMs: 5,
  })
  poller.start()
  await sleep(50)
  poller.stop()
  assert(maxInFlight <= 1, 'poller never overlaps refresh requests')
}

// 8. queued files are not reported as Used unless backend listed them
assert(
  usedFilesFromDoneEvent({
    workspace_files_injected: true,
    workspace_files_count: 1,
    workspace_files_used: [],
  }).length === 0,
  'empty used list is not fabricated from count'
)
assert(
  usedFilesFromDoneEvent({
    workspace_files_used: [{ id: '2b595b7e-88e5-4c45-9841-c639450520bb', name: 'phase4b_scheduler_canary_20260816.txt' }],
  }).every((f) => f.name !== 'queued.txt' && f.id !== '0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4'),
  'queued file is not introduced when absent from used list'
)

// 9. READY injected file is reported as Used
{
  const used = usedFilesFromDoneEvent({
    workspace_files_injected: true,
    workspace_files_used: [
      { id: '2b595b7e-88e5-4c45-9841-c639450520bb', name: 'phase4b_scheduler_canary_20260816.txt' },
    ],
  })
  assert(used.length === 1, 'one injected file')
  assert(used[0].name === 'phase4b_scheduler_canary_20260816.txt', 'injected filename')
}

// Live defect: used list is source of truth even when injected flag is missing/false
{
  const used = usedFilesFromDoneEvent({
    workspace_files_used: [
      { id: '2b595b7e-88e5-4c45-9841-c639450520bb', name: 'phase4b_scheduler_canary_20260816.txt' },
    ],
  })
  assert(used.length === 1, 'missing injected flag still uses workspace_files_used')
  assert(used[0].name === 'phase4b_scheduler_canary_20260816.txt', 'live used name without injected flag')
}
assert(
  usedFilesFromDoneEvent({
    workspace_files_injected: false,
    workspace_files_used: [
      { id: '2b595b7e-88e5-4c45-9841-c639450520bb', name: 'phase4b_scheduler_canary_20260816.txt' },
    ],
  }).length === 1,
  'injected=false does not drop a backend used list'
)
assert(usedFilesFromDoneEvent({}).length === 0, 'empty event has no used files')
assert(usedFilesFromDoneEvent(null).length === 0, 'null event has no used files')

// 10. zero injected files produces no fabricated Used list
assert(usedFilesFromDoneEvent({ workspace_files_injected: true }).length === 0, 'missing used array')
assert(
  usedFilesFromDoneEvent({
    workspace_files_injected: true,
    workspace_files_count: 3,
    workspace_files_used: [{ name: 'inferred-only.txt' }],
  }).length === 0,
  'name without id is not used'
)
assert(
  usedFilesFromDoneEvent({
    workspace_files_used: [
      { id: '2b595b7e-88e5-4c45-9841-c639450520bb', name: 'phase4b_scheduler_canary_20260816.txt' },
      { name: 'queued.txt' },
      { id: 'foreign', name: '' },
      'not-an-object',
    ],
  }).map((f) => f.name).join(',') === 'phase4b_scheduler_canary_20260816.txt',
  'invalid entries and queued/foreign names without id are dropped'
)
assert(unavailableChatNote(0) === '', 'no unavailable note when count is 0')
assert(unavailableChatNote(1).includes('not available'), 'unavailable note for queued/processing')
assert(app.includes('Used files:'), 'standard chat renders Used files')
assert(app.includes('usedFilesFromDoneEvent(event)'), 'standard chat done uses backend used list')
assert(app.includes('isStandardChatAssistant(m)'), 'standard chat render allows live and persisted chat')
assert(!app.includes('workspace_files_used') || app.includes('usedFilesFromDoneEvent'), 'no raw inference')

const threadsApi = readFileSync(join(root, 'src/api/threads.js'), 'utf8')
assert(threadsApi.includes('sanitizeUsedFiles(m.used_files)'), 'mapApiMessage restores used_files')
assert(threadsApi.includes('unavailableChatNote(m.unavailable_count)'), 'mapApiMessage restores unavailable note')

{
  const restored = {
    role: 'assistant',
    kind: 'chat',
    used_files: sanitizeUsedFiles([
      { id: '2b595b7e-88e5-4c45-9841-c639450520bb', name: 'phase4b_scheduler_canary_20260816.txt' },
      { name: 'inferred-only.txt' },
    ]),
    workspace_files_unavailable_note: unavailableChatNote(1),
  }
  assert(restored.used_files.length === 1, 'hydrate keeps only id+name used files')
  assert(restored.used_files[0].name === 'phase4b_scheduler_canary_20260816.txt', 'hydrate ready name')
  assert(restored.workspace_files_unavailable_note.includes('not available'), 'hydrate unavailable note')
  assert(isStandardChatAssistant(restored) === true, 'persisted kind=chat still renders Used files')
  assert(
    isStandardChatAssistant({ role: 'assistant', used_files: restored.used_files }) === true,
    'live assistant without kind still renders Used files'
  )
  assert(
    isStandardChatAssistant({ role: 'assistant', kind: 'adhoc_expert', used_files: restored.used_files }) === false,
    'expert bubbles do not use standard Used files'
  )
  assert(sanitizeUsedFiles(undefined).length === 0, 'old envelopes without used_files stay empty')
  assert(unavailableChatNote(undefined) === '', 'old envelopes have no fabricated note')
  assert(unavailableChatNote(1).includes('not available'), 'unavailable note still works after hydrate')
}

console.log('OK: Gate 1 file status honesty checks passed')
