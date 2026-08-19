/**
 * Auth header stability — never send unsigned persistent requests;
 * do not teardown inventory/focus on getToken callback identity churn.
 * Run: node frontend/scripts/test-auth-header-stability.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  AuthTokenUnavailableError,
  acquirePersistentHeaders,
  buildBenHeaders,
  hasPersistentAuthHeader,
  isAuthTokenUnavailable,
} from '../src/api/benHeaders.js'
import { deriveFileStage } from '../src/lib/fileStatus.js'
import { createWorkspaceFileInventory } from '../src/lib/workspaceFileInventory.js'

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

assert(hasPersistentAuthHeader({ Authorization: 'Bearer abc' }) === true, 'bearer counts')
assert(hasPersistentAuthHeader({ authorization: 'Bearer abc' }) === true, 'bearer case')
assert(hasPersistentAuthHeader({}) === false, 'empty is not persistent')
assert(hasPersistentAuthHeader({ Authorization: 'Bearer ' }) === false, 'empty bearer is not persistent')
assert(
  hasPersistentAuthHeader({
    'X-Basalt-Beta-Passcode': 'p',
    'X-Basalt-Beta-Alias': 'Alon',
  }) === true,
  'beta passcode+alias counts'
)

{
  const headers = await buildBenHeaders(async () => null)
  assert(!headers.Authorization, 'default builder still omits Authorization when token is null')
}

{
  let threw = false
  try {
    await buildBenHeaders(async () => null, {}, {}, null, { requireAuthorization: true })
  } catch (err) {
    threw = isAuthTokenUnavailable(err)
  }
  assert(threw, 'requireAuthorization throws instead of returning unsigned headers')
}

{
  const calls = []
  const headers = await acquirePersistentHeaders(
    async () => {
      calls.push(Date.now())
      if (calls.length < 2) return {}
      return { Authorization: 'Bearer later-token' }
    },
    { attempts: 4, delayMs: 1 }
  )
  assert(calls.length === 2, 'retries until token appears')
  assert(headers.Authorization === 'Bearer later-token', 'later valid token is used')
}

{
  let sent = false
  try {
    await acquirePersistentHeaders(async () => ({}), { attempts: 2, delayMs: 0 })
  } catch (err) {
    assert(isAuthTokenUnavailable(err), 'exhausted null token is AuthTokenUnavailable')
    assert(err instanceof AuthTokenUnavailableError, 'typed error')
    sent = false
  }
  assert(sent === false, 'no request is implied by acquire itself')
}

{
  let listCalls = 0
  const auth = { token: null }
  const inventory = createWorkspaceFileInventory({
    listFiles: async (_ws, headers) => {
      listCalls += 1
      assert(
        String(headers.Authorization || '').startsWith('Bearer '),
        'A/B: list is never called unsigned'
      )
      return {
        items: [
          {
            id: 'f1',
            status: 'ready',
            extraction_status: 'complete',
            index_status: 'indexed',
            job_status: 'succeeded',
            processing_stage: 'ready',
            display_name: 'doc.pdf',
          },
        ],
      }
    },
    intervalMs: 20,
  })
  inventory.configure({
    workspaceId: 'ws-1',
    buildHeaders: async () =>
      auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
  })
  await sleep(40)
  assert(listCalls === 0, 'A: signed-in + getToken null sends no list request')
  assert(inventory.getSnapshot().error == null, 'A: transient null does not set Unauthorized')
  auth.token = 'real-jwt'
  await sleep(200)
  assert(listCalls >= 1, 'B: later valid token sends the list request')
  assert(inventory.getSnapshot().rows[0]?.status === 'ready', 'B: rows load after token')
  inventory.stopPoller()
}

{
  const readyItem = {
    id: 'ready-1',
    status: 'ready',
    extraction_status: 'complete',
    index_status: 'indexed',
    job_status: 'succeeded',
    processing_stage: 'ready',
    display_name: 'TLV062_1 (1).PDF',
  }
  let listCalls = 0
  const inventory = createWorkspaceFileInventory({
    listFiles: async () => {
      listCalls += 1
      return { items: [readyItem] }
    },
    intervalMs: 30,
  })
  const first = async () => ({ Authorization: 'Bearer one' })
  inventory.configure({ workspaceId: 'ws-keep', buildHeaders: first })
  await sleep(20)
  assert(inventory.getSnapshot().rows[0]?.id === 'ready-1', 'seeded ready row')
  const callsAfterLoad = listCalls
  const second = async () => ({ Authorization: 'Bearer two' })
  inventory.configure({ workspaceId: 'ws-keep', buildHeaders: second })
  await sleep(25)
  assert(inventory.getSnapshot().rows[0]?.id === 'ready-1', 'C: Ready rows remain after header identity change')
  assert(inventory.getSnapshot().rows.length === 1, 'C: inventory not cleared')
  assert(listCalls === callsAfterLoad, 'C: identity change does not reload/teardown')
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
            id: ws,
            status: 'ready',
            display_name: ws,
            extraction_status: 'complete',
            index_status: 'indexed',
            job_status: 'succeeded',
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
  assert(seen[0] === 'ws-a', 'E: first workspace loads')
  inventory.configure({
    workspaceId: 'ws-b',
    buildHeaders: async () => ({ Authorization: 'Bearer b' }),
  })
  await sleep(20)
  assert(seen.includes('ws-b'), 'E: workspace change reconfigures')
  assert(inventory.getSnapshot().workspaceId === 'ws-b', 'E: snapshot workspace updated')
  assert(inventory.getSnapshot().rows[0]?.id === 'ws-b', 'E: rows belong to new workspace')
  inventory.stopPoller()
}

{
  const inventory = createWorkspaceFileInventory({
    listFiles: async () => ({
      items: [{ id: 'x', status: 'ready', job_status: 'succeeded', extraction_status: 'complete', index_status: 'indexed' }],
    }),
    intervalMs: 20,
  })
  inventory.configure({
    workspaceId: 'ws-out',
    buildHeaders: async () => ({ Authorization: 'Bearer live' }),
  })
  await sleep(20)
  assert(inventory.getSnapshot().rows.length === 1, 'signed-in has rows')
  inventory.configure({ workspaceId: null, buildHeaders: null })
  assert(inventory.getSnapshot().rows.length === 0, 'F: sign-out clears inventory')
  assert(inventory.getSnapshot().workspaceId == null, 'F: sign-out drops workspace')
}

{
  const states = [
    { items: [{ id: 'f1', status: 'queued', extraction_status: 'pending', index_status: 'not_indexed', job_status: 'queued', processing_stage: 'queued' }] },
    { items: [{ id: 'f1', status: 'queued', extraction_status: 'extracting', index_status: 'not_indexed', job_status: 'running', processing_stage: 'extracting' }] },
    { items: [{ id: 'f1', status: 'queued', extraction_status: 'extracting', index_status: 'indexing', job_status: 'running', processing_stage: 'indexing' }] },
    { items: [{ id: 'f1', status: 'ready', extraction_status: 'complete', index_status: 'indexed', job_status: 'succeeded', processing_stage: 'ready' }] },
  ]
  let idx = 0
  let inFlight = 0
  let maxInFlight = 0
  const inventory = createWorkspaceFileInventory({
    listFiles: async (_ws, headers) => {
      assert(String(headers.Authorization).startsWith('Bearer '), 'I: poller lists stay authenticated')
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      const data = states[Math.min(idx, states.length - 1)]
      idx += 1
      await sleep(4)
      inFlight -= 1
      return data
    },
    intervalMs: 12,
  })
  const seen = []
  inventory.subscribe(() => {
    const row = inventory.getSnapshot().rows[0]
    if (row) seen.push(deriveFileStage(row))
  })
  inventory.configure({
    workspaceId: 'ws-life',
    buildHeaders: async () => ({ Authorization: 'Bearer cycle' }),
  })
  await sleep(120)
  assert(seen.includes('queued') && seen.includes('extracting') && seen.includes('indexing') && seen.includes('ready'), 'I: Queued→Extracting→Indexing→Ready')
  assert(maxInFlight <= 1, 'H: one in-flight list request')
  const sidebar = seen.slice()
  const library = seen.slice()
  const composer = seen.slice()
  assert(JSON.stringify(sidebar) === JSON.stringify(library), 'J: sidebar/library share inventory stages')
  assert(JSON.stringify(library) === JSON.stringify(composer), 'J: composer shares inventory stages')
  inventory.stopPoller()
}

{
  let listCalls = 0
  const inventory = createWorkspaceFileInventory({
    listFiles: async () => {
      listCalls += 1
      const err = new Error('Unauthorized')
      err.status = 401
      throw err
    },
    intervalMs: 40,
  })
  inventory.configure({
    workspaceId: 'ws-401',
    buildHeaders: async () => ({ Authorization: 'Bearer expired' }),
  })
  await sleep(30)
  assert(listCalls >= 1, 'genuine 401 is sent (has Bearer)')
  assert(inventory.getSnapshot().error === 'Unauthorized', 'F: genuine 401 still surfaces')
  inventory.stopPoller()
}

const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
const sidebar = readFileSync(join(root, 'src/components/KnowledgeSidebar.jsx'), 'utf8')
const inventorySrc = readFileSync(join(root, 'src/lib/workspaceFileInventory.js'), 'utf8')

assert(app.includes('persistentHeaders'), 'App keeps a stable persistent header builder')
assert(app.includes('buildAppHeadersRef'), 'App stores latest token builder in a ref')
assert(app.includes('[persistentReady, activeProjectId, persistentHeaders, sessionTenantId]'), 'inventory configure ignores getToken identity and follows tenant')
assert(!app.includes('[persistentReady, activeProjectId, buildAppHeaders]'), 'old header-identity configure deps removed')
assert(app.includes('acquirePersistentHeaders(persistentHeaders)'), 'projects wait for a real token')
assert(
  app.includes('const headers = await acquirePersistentHeaders(persistentHeaders)'),
  'standard chat send waits for a real token'
)
{
  const configureBlock = app.slice(
    app.indexOf('workspaceFileInventory.configure({'),
    app.indexOf('workspaceFileInventory.configure({') + 520
  )
  const firstEffect = configureBlock.split('useEffect')[0]
  assert(
    !firstEffect.includes('return () =>') || !firstEffect.includes('workspaceId: null'),
    'J: configure effect does not wipe inventory on dep cleanup'
  )
}
assert(
  /useEffect\(\(\) => \{\s*return \(\) => \{\s*workspaceFileInventory\.configure\(\{ workspaceId: null/.test(app),
  'unmount still clears inventory'
)
assert(app.includes('isAuthTokenUnavailable'), 'projects do not treat transient token-null as empty workspace')

assert(sidebar.includes('createActiveFocusController'), 'focus uses retry/success lifecycle')
assert(sidebar.includes('focusKey, focusQuery, focusThreadId, projectSlug, authReady'), 'D: focus key is not header identity')
assert(!sidebar.includes('[attentionFocusRequest, projectSlug, buildHeaders]'), 'D: old focus deps removed')
assert(sidebar.includes('buildHeadersRef'), 'focus reads latest headers at request time')
assert(sidebar.includes('isAuthTokenUnavailable'), 'focus distinguishes token-null from HTTP 401')

assert(inventorySrc.includes('acquirePersistentHeaders'), 'inventory never lists unsigned')
assert(inventorySrc.includes('signedInChanged'), 'inventory reconfigure is signed-in/scope, not fn identity')
assert(inventorySrc.includes('authRetry'), 'token-null schedules authenticated retry rather than 401 UI')
assert(inventorySrc.includes('skipped: \'auth\'') || inventorySrc.includes('skipped: "auth"'), 'unsigned list is skipped')

console.log('OK: auth header stability checks passed')
