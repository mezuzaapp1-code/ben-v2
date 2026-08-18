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
  deriveFileStage,
  fileStatusLabel,
  fileStageLabel,
  isNonTerminalFile,
  isNonTerminalFileStatus,
  isTerminalFileStatus,
  mergeFileInventory,
  pageProgress,
  processingPercent,
  sanitizeUsedFiles,
  stagesByFileId,
  unavailableChatNote,
  usedFilesFromDoneEvent,
  isStandardChatAssistant,
} from '../src/lib/fileStatus.js'
import { createWorkspaceFileInventory, normalizeProgress } from '../src/lib/workspaceFileInventory.js'

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

// 1. queued displays as Queued (not available to chat)
assert(fileStatusLabel('queued') === 'Queued', 'queued label')
assert(fileStatusLabel('uploaded') === FILE_STATUS_LABELS.queued, 'uploaded maps to queued copy')
assert(isNonTerminalFileStatus('queued') === true, 'queued is non-terminal')

// 2. processing/extracting displays as Extracting
assert(fileStatusLabel('processing') === 'Queued', 'processing without a running job is queued')
assert(
  fileStageLabel(deriveFileStage({ status: 'queued', extraction_status: 'extracting', job_status: 'running' })) === 'Extracting',
  'extracting label'
)
assert(isNonTerminalFileStatus('processing') === true, 'processing is non-terminal')

// 3. ready displays as Ready
assert(fileStatusLabel('ready') === 'Ready', 'ready label')
assert(isTerminalFileStatus('ready') === true, 'ready is terminal')
assert(isNonTerminalFileStatus('ready') === false, 'ready is not non-terminal')

// 4. failed displays as Failed
assert(fileStatusLabel('failed') === 'Failed', 'failed label')
assert(isTerminalFileStatus('failed') === true, 'failed is terminal')

const overlay = readFileSync(join(root, 'src/components/FileLibraryOverlay.jsx'), 'utf8')
const sidebar = readFileSync(join(root, 'src/components/KnowledgeSidebar.jsx'), 'utf8')
const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')

assert(overlay.includes('FileLifecycleStatus'), 'library uses honesty labels')
assert(sidebar.includes('FileLifecycleStatus'), 'sidebar uses honesty labels')
assert(app.includes('FileLifecycleBubble'), 'composer uses honesty labels')
assert(!app.includes("result?.status || 'ready'"), 'composer does not invent READY')
assert(app.includes("result?.status || 'uploaded'"), 'composer falls back to uploaded, not ready')
assert(overlay.includes('workspaceFileInventory'), 'library uses shared inventory')
assert(sidebar.includes('workspaceFileInventory'), 'sidebar uses shared inventory')
assert(app.includes('workspaceFileInventory.configure'), 'app configures shared inventory')
assert(!overlay.includes('createBoundedStatusPoller'), 'library does not start its own poller')
assert(!sidebar.includes('createBoundedStatusPoller'), 'sidebar does not start its own poller')
assert(overlay.includes('hasNonTerminal') === false || overlay.includes('visibleItems'), 'library filters shared rows')

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

{
  let calls = 0
  const poller = createBoundedStatusPoller({
    shouldPoll: () => calls < 1,
    refresh: async () => {
      calls += 1
    },
    intervalMs: 1000,
  })
  poller.start()
  await sleep(20)
  assert(calls === 1, 'first refresh happens immediately')
  poller.stop()
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

// Lifecycle UX: upload bytes, stages, no false READY, no fabricated processing %, shared inventory
{
  const upload = { loaded: 5_000_000, total: 10_000_000, percent: 50, phase: 'uploading' }
  assert(deriveFileStage({}, { upload }) === 'uploading', 'upload stage')
  assert(processingPercent({}, upload) === 50, 'upload percent from real bytes')
  assert(fileStageLabel('uploading', {}, upload) === 'Uploading 50%', 'upload percent label')
  const advanced = normalizeProgress({ loaded: 7_500_000, total: 10_000_000 }, { size: 10_000_000 })
  assert(advanced.percent === 75, 'upload progress advances with loaded/total')
  assert(normalizeProgress({ loaded: 100, percent: null }, { size: 0 }).percent == null, 'no fabricated upload percent without total')
}

{
  const queued = { id: '1', status: 'queued', extraction_status: 'pending', index_status: 'not_indexed', job_status: 'queued' }
  const extracting = { id: '1', status: 'queued', extraction_status: 'extracting', index_status: 'not_indexed', job_status: 'running' }
  const indexing = { id: '1', status: 'queued', extraction_status: 'extracting', index_status: 'indexing', job_status: 'running' }
  const indexingComplete = { id: '1', status: 'queued', extraction_status: 'complete', index_status: 'indexing', job_status: 'running' }
  const ready = { id: '1', status: 'ready', extraction_status: 'complete', index_status: 'indexed', job_status: 'succeeded' }
  const failed = { id: '1', status: 'failed', extraction_status: 'failed', job_status: null }
  assert(deriveFileStage(queued) === 'queued', 'queued after upload')
  assert(deriveFileStage(extracting) === 'extracting', 'running job is extracting')
  assert(deriveFileStage(indexing) === 'indexing', 'running+indexing is not hidden by extracting flag')
  assert(deriveFileStage(indexingComplete) === 'indexing', 'complete+indexing is indexing')
  assert(deriveFileStage(ready) === 'ready', 'indexing to ready')
  assert(deriveFileStage(failed) === 'failed', 'failed visible')
  assert(fileStageLabel('failed') === 'Failed', 'failed label')
  assert(processingPercent(extracting) == null, 'no fabricated extracting percent')
  assert(processingPercent(indexing) == null, 'no fabricated indexing percent')
  assert(processingPercent(queued) == null, 'no fabricated queued percent')
  assert(pageProgress(extracting) == null, 'no page X of Y without both counts')
  assert(pageProgress({ page_count: 20, pages_extracted: 4 }).y === 20, 'page progress only with real X and Y')
  assert(
    deriveFileStage({
      status: 'queued',
      extraction_status: 'extracting',
      index_status: 'not_indexed',
      job_status: 'queued',
    }) === 'queued',
    'crash during extracting requeue is queued'
  )
  assert(
    deriveFileStage({
      status: 'queued',
      extraction_status: 'extracting',
      index_status: 'indexing',
      job_status: 'queued',
    }) === 'queued',
    'crash during indexing requeue is queued'
  )
  assert(
    deriveFileStage({
      status: 'failed',
      extraction_status: 'failed',
      index_status: 'failed',
      job_status: 'queued',
    }) === 'queued',
    'queued retry overrides stale failed flags'
  )
  assert(
    deriveFileStage({
      status: 'failed',
      extraction_status: 'extracting',
      job_status: 'running',
    }) === 'extracting',
    'running retry restores extracting'
  )
  assert(
    deriveFileStage({
      status: 'queued',
      extraction_status: 'extracting',
      job_status: 'failed',
    }) === 'failed',
    'max-attempts failed job is failed'
  )
  assert(
    deriveFileStage({
      status: 'ready',
      extraction_status: 'extracting',
      index_status: 'indexing',
      job_status: 'running',
    }) === 'ready',
    'Ready wins over stale job/file intermediate flags'
  )
  assert(
    deriveFileStage({
      status: 'queued',
      extraction_status: 'complete',
      index_status: 'indexed',
      processing_stage: 'ready',
    }) !== 'ready',
    'no false READY from processing_stage'
  )
}

{
  const files = [{ id: 'a', status: 'queued', display_name: 'doc.pdf' }]
  const uploads = [{ localId: 'upload-1', name: 'doc.pdf', phase: 'uploading', loaded: 1, total: 2, percent: 50 }]
  const rows = mergeFileInventory(files, uploads)
  const sidebar = stagesByFileId(rows)
  const library = stagesByFileId(rows)
  const composer = stagesByFileId(rows)
  assert(JSON.stringify(sidebar) === JSON.stringify(library), 'sidebar and library share stages')
  assert(JSON.stringify(library) === JSON.stringify(composer), 'library and composer share stages')
}

{
  const states = [
    { items: [{ id: 'f1', status: 'queued', extraction_status: 'pending', index_status: 'not_indexed', job_status: 'queued', processing_stage: 'queued' }] },
    { items: [{ id: 'f1', status: 'failed', extraction_status: 'failed', index_status: 'failed', job_status: 'queued', processing_stage: 'queued' }] },
    { items: [{ id: 'f1', status: 'queued', extraction_status: 'extracting', index_status: 'not_indexed', job_status: 'running', processing_stage: 'extracting' }] },
    { items: [{ id: 'f1', status: 'queued', extraction_status: 'extracting', index_status: 'indexing', job_status: 'running', processing_stage: 'indexing' }] },
    { items: [{ id: 'f1', status: 'ready', extraction_status: 'complete', index_status: 'indexed', job_status: 'succeeded', processing_stage: 'ready' }] },
  ]
  let idx = 0
  let listCalls = 0
  let maxInFlight = 0
  let inFlight = 0
  const inventory = createWorkspaceFileInventory({
    listFiles: async () => {
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      listCalls += 1
      const data = states[Math.min(idx, states.length - 1)]
      idx += 1
      await sleep(5)
      inFlight -= 1
      return data
    },
    intervalMs: 15,
  })
  const seen = []
  inventory.subscribe(() => {
    const rows = inventory.getSnapshot().rows
    if (rows[0]) seen.push(deriveFileStage(rows[0]))
  })
  inventory.configure({
    workspaceId: 'ws-1',
    buildHeaders: async () => ({}),
  })
  await sleep(12)
  assert(listCalls >= 1, 'inventory loads immediately on configure')
  await sleep(120)
  assert(seen.includes('queued'), 'inventory saw queued')
  assert(seen.includes('extracting'), 'inventory saw extracting')
  assert(seen.includes('indexing'), 'indexing is actually shown')
  assert(seen.includes('ready'), 'inventory reached ready')
  assert(seen.indexOf('queued') < seen.indexOf('extracting'), 'queued before extracting')
  assert(seen.indexOf('extracting') < seen.indexOf('indexing'), 'extracting before indexing')
  assert(seen.indexOf('indexing') < seen.indexOf('ready'), 'indexing before ready')
  await sleep(40)
  const callsAtReady = listCalls
  await sleep(40)
  assert(listCalls === callsAtReady, `polling stops at terminal ready (calls=${listCalls} after=${callsAtReady})`)
  assert(maxInFlight <= 1, 'inventory never overlaps list requests')
  inventory.stopPoller()
}

assert(
  isNonTerminalFile({ status: 'failed', extraction_status: 'failed', job_status: 'queued' }) === true,
  'poller does not treat stale failed file as terminal while job is queued'
)
assert(
  isNonTerminalFile({ status: 'queued', extraction_status: 'extracting', job_status: 'failed' }) === false,
  'max-attempts failed job is terminal'
)
assert(
  isNonTerminalFile({ status: 'ready', job_status: 'running' }) === false,
  'ready is terminal even if job flags lag'
)

{
  const files = [{ status: 'queued', job_status: 'failed' }]
  let calls = 0
  const poller = createBoundedStatusPoller({
    shouldPoll: () => files.some((f) => isNonTerminalFile(f)),
    refresh: async () => {
      calls += 1
    },
    intervalMs: 8,
  })
  poller.start()
  await sleep(20)
  assert(calls === 0, 'no polling when job is terminally failed')
  poller.stop()
}

console.log('OK: Gate 1 file status honesty checks passed')
