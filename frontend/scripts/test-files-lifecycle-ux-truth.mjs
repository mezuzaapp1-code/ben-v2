/**
 * Files lifecycle UX truthfulness: File Library empty copy, Focus retry,
 * chat send auth, inventory configure cleanup, shared canonical IDs.
 * Run: node frontend/scripts/test-files-lifecycle-ux-truth.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  acquirePersistentHeaders,
  isAuthTokenUnavailable,
} from '../src/api/benHeaders.js'
import { createActiveFocusController } from '../src/lib/activeFocusSession.js'
import {
  FILE_LIBRARY_EMPTY,
  FILE_LIBRARY_REOPEN_RESETS_TO_ALL,
  fileLibraryEmptyMessage,
  filterLibraryItems,
} from '../src/lib/fileLibraryView.js'
import { createWorkspaceFileInventory } from '../src/lib/workspaceFileInventory.js'
import { stagesByFileId } from '../src/lib/fileStatus.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

const readyRow = {
  id: 'cb0558d3-637f-4f6a-8446-8a393874cde0',
  display_name: 'TLV062_1 (1).PDF',
  original_filename: 'TLV062_1 (1).PDF',
  status: 'ready',
  extraction_status: 'complete',
  index_status: 'indexed',
  job_status: 'succeeded',
  processing_stage: 'ready',
}

const overlay = readFileSync(join(root, 'src/components/FileLibraryOverlay.jsx'), 'utf8')
const sidebar = readFileSync(join(root, 'src/components/KnowledgeSidebar.jsx'), 'utf8')
const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
const bubble = readFileSync(join(root, 'src/components/FileLifecycleStatus.jsx'), 'utf8')

assert(FILE_LIBRARY_REOPEN_RESETS_TO_ALL === true, 'E: reopen behavior is explicitly A (reset to All files)')
assert(overlay.includes('FILE_LIBRARY_REOPEN_RESETS_TO_ALL'), 'E: overlay honors reopen constant')
assert(overlay.includes('[open]'), 'E: overlay resets on open change')
assert(overlay.includes("setView('all')"), 'E: reopen sets All files')
assert(overlay.includes("setQ('')"), 'E: reopen clears search needle')

{
  const visible = filterLibraryItems([readyRow], 'all', '')
  assert(visible.length === 1, 'A: All files shows Ready row')
  assert(visible[0].id === readyRow.id, 'A: Ready file id preserved')
  assert(
    fileLibraryEmptyMessage({
      workspaceId: 'd8a62b75-d8e3-45ca-a8e7-e28e24535072',
      view: 'all',
      query: '',
      inventoryCount: 1,
      visibleCount: visible.length,
    }) == null,
    'A: All files with rows has no empty copy'
  )
}

{
  const visible = filterLibraryItems([readyRow], 'processing', '')
  assert(visible.length === 0, 'B: Processing hides Ready files')
  const copy = fileLibraryEmptyMessage({
    workspaceId: 'ws',
    view: 'processing',
    query: '',
    inventoryCount: 1,
    visibleCount: visible.length,
  })
  assert(copy === FILE_LIBRARY_EMPTY.processing, 'B: Processing empty copy')
  assert(copy === 'No files currently processing.', 'B: exact Processing copy')
  assert(!String(copy).includes('No files yet'), 'B: Processing does not claim library is empty')
}

{
  const visible = filterLibraryItems([readyRow], 'failed', '')
  assert(visible.length === 0, 'C: Failed hides Ready files')
  const copy = fileLibraryEmptyMessage({
    workspaceId: 'ws',
    view: 'failed',
    query: '',
    inventoryCount: 1,
    visibleCount: 0,
  })
  assert(copy === 'No failed files.', 'C: Failed empty-state honesty')
  assert(!String(copy).includes('No files yet'), 'C: Failed does not claim library is empty')
}

{
  const visible = filterLibraryItems([readyRow], 'all', 'definitely-not-this-name')
  assert(visible.length === 0, 'D: search with zero matches hides the row')
  const copy = fileLibraryEmptyMessage({
    workspaceId: 'ws',
    view: 'all',
    query: 'definitely-not-this-name',
    inventoryCount: 1,
    visibleCount: 0,
  })
  assert(copy === 'No matching files.', 'D: search zero matches copy')
}

{
  const copy = fileLibraryEmptyMessage({
    workspaceId: 'ws',
    view: 'all',
    query: '',
    inventoryCount: 0,
    visibleCount: 0,
  })
  assert(copy === FILE_LIBRARY_EMPTY.inventoryEmpty, 'true inventory empty uses upload copy')
}

{
  let fetchCalls = 0
  let seenAuth = []
  const auth = { token: 'expired-jwt' }
  const controller = createActiveFocusController({
    retryDelayMs: 15,
    acquireHeaders: async () => acquirePersistentHeaders(
      async () => (auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
      { attempts: 2, delayMs: 0 }
    ),
    fetchFocus: async (_slug, _tid, _q, headers) => {
      fetchCalls += 1
      seenAuth.push(String(headers.Authorization || ''))
      if (headers.Authorization === 'Bearer expired-jwt') {
        const err = new Error('Unauthorized')
        err.status = 401
        throw err
      }
      return { has_focus: true, grouped: { documentation: [{ entity_name: 'doc', score_percent: 80 }] } }
    },
  })
  const seenErrors = []
  controller.subscribe(() => {
    seenErrors.push(controller.getSnapshot().error)
  })
  controller.start({ projectSlug: 'amazon', threadId: 't1', query: 'hello' })
  await sleep(40)
  assert(fetchCalls >= 1, 'F: Focus request is sent when Bearer is present')
  assert(controller.getSnapshot().error === 'Unauthorized', 'F: authenticated 401 is visible')
  auth.token = 'fresh-jwt'
  await sleep(80)
  assert(seenAuth.includes('Bearer fresh-jwt'), 'G: later token retries Focus')
  assert(controller.getSnapshot().error == null, 'G: later authenticated Focus 200 clears focusError')
  assert(controller.getSnapshot().data?.has_focus === true, 'G: Focus data stored on success')
  controller.stop()
}

{
  let posted = 0
  let postedAuth = null
  const auth = { token: null }
  const pending = (async () => {
    const headers = await acquirePersistentHeaders(
      async () => (auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
      { attempts: 8, delayMs: 20 }
    )
    posted += 1
    postedAuth = headers.Authorization
    return headers
  })()
  await sleep(50)
  assert(posted === 0, 'H: signed-in + getToken null sends no POST')
  auth.token = 'chat-jwt'
  await pending
  assert(posted === 1, 'I: later token sends chat')
  assert(postedAuth === 'Bearer chat-jwt', 'I: chat POST sends Bearer')
}

{
  const inventory = createWorkspaceFileInventory({
    listFiles: async () => ({ items: [readyRow] }),
    intervalMs: 40,
  })
  inventory.configure({
    workspaceId: 'd8a62b75-d8e3-45ca-a8e7-e28e24535072',
    buildHeaders: async () => ({ Authorization: 'Bearer one' }),
  })
  await sleep(20)
  const first = async () => ({ Authorization: 'Bearer one' })
  const second = async () => ({ Authorization: 'Bearer two' })
  inventory.configure({
    workspaceId: 'd8a62b75-d8e3-45ca-a8e7-e28e24535072',
    buildHeaders: first,
  })
  inventory.configure({
    workspaceId: 'd8a62b75-d8e3-45ca-a8e7-e28e24535072',
    buildHeaders: second,
  })
  await sleep(20)
  assert(inventory.getSnapshot().rows[0]?.id === readyRow.id, 'J: header callback churn does not wipe rows')
  inventory.stopPoller()
}

{
  let seen = []
  const inventory = createWorkspaceFileInventory({
    listFiles: async (ws) => {
      seen.push(ws)
      return {
        items: [
          {
            ...readyRow,
            id: ws === 'ws-a' ? 'file-a' : 'file-b',
            display_name: ws,
          },
        ],
      }
    },
    intervalMs: 40,
  })
  inventory.configure({
    workspaceId: 'ws-a',
    buildHeaders: async () => ({ Authorization: 'Bearer a' }),
  })
  await sleep(20)
  assert(inventory.getSnapshot().rows[0]?.id === 'file-a', 'K: first workspace rows')
  inventory.configure({
    workspaceId: 'ws-b',
    buildHeaders: async () => ({ Authorization: 'Bearer b' }),
  })
  await sleep(20)
  assert(inventory.getSnapshot().workspaceId === 'ws-b', 'K: snapshot workspace updated')
  assert(inventory.getSnapshot().rows.every((row) => row.id === 'file-b'), 'K: previous workspace rows removed')
  assert(!inventory.getSnapshot().rows.some((row) => row.id === 'file-a'), 'K: no cross-workspace residue')
  inventory.stopPoller()
}

{
  const files = [readyRow]
  const rows = files
  const sidebarIds = rows.map((row) => row.id)
  const libraryIds = filterLibraryItems(rows, 'all', '').map((row) => row.id)
  const composerIds = Object.keys(stagesByFileId(rows))
  assert(JSON.stringify(sidebarIds) === JSON.stringify(libraryIds), 'L: Sidebar All files IDs match Library')
  assert(JSON.stringify(libraryIds) === JSON.stringify(composerIds), 'L: Composer shares the same canonical IDs')
}

assert(overlay.includes('useWorkspaceFileInventory'), 'L: Library consumes shared inventory')
assert(sidebar.includes('useWorkspaceFileInventory'), 'L: Sidebar consumes shared inventory')
assert(bubble.includes('useWorkspaceFileInventory'), 'L: Composer bubble consumes shared inventory')
assert(overlay.includes('items = inventory.rows'), 'L: Library rows are inventory.rows')
assert(sidebar.includes('inventory.rows'), 'L: Sidebar rows are inventory.rows')
assert(app.includes('workspaceFileInventory.configure'), 'L: App owns the shared configure')
assert(
  !overlay.includes('createWorkspaceFileInventory'),
  'L: Library does not create an independent inventory'
)

assert(app.includes('const headers = await acquirePersistentHeaders(persistentHeaders)'), 'chat send acquires headers')
assert(app.includes('for await (const event of postChatStream({'), 'chat still POSTs through postChatStream')

{
  const sendIdx = app.indexOf('const send = useCallback')
  const sendBody = app.slice(sendIdx, app.indexOf('applyCouncilMessages', sendIdx))
  assert(sendBody.includes('acquirePersistentHeaders(persistentHeaders)'), 'H/I: send acquires before POST')
  assert(!sendBody.includes('const headers = await buildAppHeaders()'), 'H/I: send no longer uses raw buildAppHeaders')
}

{
  const firstConfigure = app.indexOf(
    'workspaceFileInventory.configure({\n      workspaceId: persistentReady'
  )
  const firstChunk = app.slice(firstConfigure, firstConfigure + 220)
  assert(!firstChunk.includes('return () =>'), 'J: live configure effect has no wipe cleanup')
}

console.log('OK: files lifecycle UX truthfulness checks passed')
