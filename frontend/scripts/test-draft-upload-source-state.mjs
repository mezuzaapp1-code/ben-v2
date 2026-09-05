/**
 * Draft-chat attach must persist a real thread UUID before upload.
 * Run: node frontend/scripts/test-draft-upload-source-state.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  adoptPersistedThreadInList,
  ensurePersistedThreadForUpload,
} from '../src/lib/ensurePersistedThread.js'
import { isPersistedThreadId, serverThreadIdForApi } from '../src/threadStorage.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const persisted = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const draftId = `draft:${persisted}`

{
  let persistCalls = 0
  const first = await ensurePersistedThreadForUpload(draftId, async () => {
    persistCalls += 1
    return { thread: { id: persisted } }
  })
  assert(persistCalls === 1, 'A: draft attach persists the thread first')
  assert(first.created === true, 'A: first attach creates the server thread')
  assert(first.threadId === persisted, 'A: upload receives the real thread UUID')
  assert(first.replacedDraftId === draftId, 'A: local draft id is replaced')
  assert(serverThreadIdForApi(first.threadId) === persisted, 'A: source_chat_id is the UUID')
  assert(serverThreadIdForApi(draftId) === undefined, 'A: draft id is never API-safe')

  const second = await ensurePersistedThreadForUpload(first.threadId, async () => {
    persistCalls += 1
    throw new Error('F: existing thread must not persist again')
  })
  assert(persistCalls === 1, 'F: already-persisted attach does not create a thread')
  assert(second.created === false, 'F: existing thread is unchanged')
  assert(second.threadId === persisted, 'F: second attach reuses the same UUID')

  const sendThreadId = serverThreadIdForApi(first.threadId)
  assert(sendThreadId === persisted, 'E: first message uses the same persisted thread')
}

{
  const threads = [
    { id: draftId, title: 'New conversation', messages: [{ role: 'user', content: 'hold' }], isDraft: true },
  ]
  const adopted = adoptPersistedThreadInList(threads, draftId, persisted)
  assert(adopted.length === 1, 'E: adopt does not add a second conversation')
  assert(adopted[0].id === persisted, 'E: local conversation id becomes the server UUID')
  assert(adopted[0].isDraft === false, 'E: adopted thread is no longer a draft')
  assert(adopted[0].messages[0].content === 'hold', 'E: local messages stay on the same thread')
}

{
  let persistCalls = 0
  const existing = await ensurePersistedThreadForUpload(persisted, async () => {
    persistCalls += 1
    return { thread: { id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' } }
  })
  assert(persistCalls === 0, 'F: persisted id does not call create')
  assert(existing.threadId === persisted, 'F: persisted id is returned as-is')
}

{
  let failed = false
  try {
    await ensurePersistedThreadForUpload(draftId, async () => ({ thread: { id: draftId } }))
  } catch (e) {
    failed = true
    assert(
      String(e.message).includes('could not be saved'),
      'persist that returns a draft id is rejected'
    )
  }
  assert(failed, 'must not treat draft:* as a persisted thread')
}

assert(isPersistedThreadId(persisted), 'UUID helper still accepts persisted ids')
assert(!isPersistedThreadId(draftId), 'UUID helper still rejects draft ids')

const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
const attach = app.slice(app.indexOf('handleWorkspaceFileAttach'), app.indexOf('handleReceiptFile'))
const sendFn = app.slice(app.indexOf('const send = useCallback'), app.indexOf('const applyCouncilMessages'))
const councilFn = app.slice(app.indexOf('const council = useCallback'), app.indexOf('const handleComposerSubmit'))
const canSend = app.slice(app.indexOf('const canSendComposer = useMemo'), app.indexOf('const handleEngineSelect'))
const composer = app.slice(app.indexOf('<ComposerCapsule'), app.indexOf('attachMenuItems={attachMenuItems}'))

assert(attach.includes('ensurePersistedThreadForUpload'), 'attach persists drafts before upload')
assert(attach.includes('createConversationThread'), 'attach reuses POST /api/threads')
assert(attach.includes('serverThreadIdForApi(tid)'), 'attach uses the API-safe UUID after persist')
assert(!attach.includes('serverThreadIdForApi(tid) || tid'), 'attach never falls back to draft:*')
assert(attach.indexOf('ensurePersistedThreadForUpload') < attach.indexOf('uploadFile'), 'persist happens before uploadFile')
assert(app.includes('threadId: apiThreadId'), 'send still uses serverThreadIdForApi for the first message')

assert(
  sendFn.includes('if (loading || fileUploading || fileAttachInFlightRef.current) return'),
  'A: send returns while persist/upload is in flight'
)
assert(
  !sendFn.includes('postChatStream') ||
    sendFn.indexOf('if (loading || fileUploading || fileAttachInFlightRef.current) return') <
      sendFn.indexOf('postChatStream'),
  'A: no chat request until attach in-flight clears'
)
assert(
  canSend.includes('if (loading || !persistentReady || fileUploading) return false'),
  'A: canSendComposer is false while file attach is in flight'
)
assert(
  composer.includes('disabled={loading || !persistentReady || fileUploading}'),
  'A: composer Send is disabled while file attach is in flight'
)
assert(
  councilFn.includes('if (!text || loading || fileUploading || fileAttachInFlightRef.current) return'),
  'C: council returns while persist/upload is in flight'
)

assert(
  attach.includes('fileAttachInFlightRef.current = true') &&
    attach.includes('setFileUploading(true)') &&
    attach.indexOf('setFileUploading(true)') < attach.indexOf('ensurePersistedThreadForUpload'),
  'D: in-flight flags rise before persist/upload on draft and persisted threads'
)
assert(
  attach.includes('fileAttachInFlightRef.current = false') &&
    attach.includes('setFileUploading(false)') &&
    attach.includes('} finally {'),
  'E: in-flight flags clear in finally so a failed upload does not leave Send disabled'
)
assert(
  sendFn.includes('const apiThreadId = serverThreadIdForApi(tid)'),
  'B: after adopt, send uses the persisted UUID only'
)

const api = readFileSync(join(root, 'src/api/workspaceFiles.js'), 'utf8')
assert(api.includes('isPersistedThreadId(sourceChatId)'), 'XHR upload drops non-UUID source_chat_id')

const threadsApi = readFileSync(join(root, 'src/api/threads.js'), 'utf8')
assert(threadsApi.includes("fetch(`${BEN_API_BASE}/api/threads`"), 'createConversationThread posts /api/threads')
assert(threadsApi.includes('method: \'POST\''), 'thread create is POST')

console.log('OK: draft upload persist-before-source-state contract')
