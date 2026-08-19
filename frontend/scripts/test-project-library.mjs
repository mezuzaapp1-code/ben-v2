/**
 * Project Library V1 UI + pagination helpers.
 * Run: node frontend/scripts/test-project-library.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { isClerkPersistentSessionReady } from '../src/auth/clerkPersistentAccess.js'
import { createWorkspaceFileInventory } from '../src/lib/workspaceFileInventory.js'
import {
  PROJECT_LIBRARY_DEFAULT_LIMIT,
  PROJECT_LIBRARY_EMPTY,
  PROJECT_LIBRARY_MAX_ITEMS,
  PROJECT_LIBRARY_REOPEN_RESETS,
  applyProjectPage,
  mergeProjectPage,
  projectLibraryEmptyMessage,
} from '../src/lib/projectLibrary.js'
import {
  activeProjectForTenant,
  applyTenantScopeChange,
  bindActiveProject,
  clearActiveProject,
  fileLibraryWorkspaceBinding,
  projectLibraryActiveCopy,
  reconcileActiveProject,
  selectActiveProject,
  workspaceBindingForSession,
} from '../src/lib/activeProject.js'
import {
  isOrganizationTenantId,
  isPersonalTenantId,
  resolveActiveTenantId,
} from '../src/lib/tenantIdentity.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const overlay = readFileSync(join(root, 'src/components/ProjectLibraryOverlay.jsx'), 'utf8')
const api = readFileSync(join(root, 'src/api/projects.js'), 'utf8')
const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
const inventorySrc = readFileSync(join(root, 'src/lib/workspaceFileInventory.js'), 'utf8')
const modal = readFileSync(join(root, 'src/components/NewProjectModal.jsx'), 'utf8')

assert(PROJECT_LIBRARY_DEFAULT_LIMIT === 50, 'default page size 50')
assert(PROJECT_LIBRARY_MAX_ITEMS === 1000, 'client memory cap')
assert(PROJECT_LIBRARY_REOPEN_RESETS === true, 'reopen resets to first page')

assert(overlay.includes('export function ProjectLibraryOverlay'), 'overlay export')
assert(overlay.includes('export function ProjectLibraryNavTrigger'), 'nav trigger')
assert(app.includes('<ProjectLibraryOverlay'), 'App wires overlay')
assert(app.includes('<ProjectLibraryNavTrigger'), 'App wires nav')
assert(app.includes('openProjectsLibrary'), 'open handler')
assert(app.includes('handleOpenProject'), 'open project handler')
assert(app.includes('bindActiveProject(sessionTenantId, project)'), 'Open stamps current tenant on active project')
assert(app.includes('setActiveProject(selected)'), 'Open updates canonical active project')
assert(app.includes('reconcileActiveProject'), 'page 1 refetch reconciles without dropping active')
assert(app.includes('applyTenantScopeChange'), 'tenant change clears canonical identity')
assert(app.includes('resolveActiveTenantId'), 'App resolves Clerk tenant identity')
assert(app.includes('[persistentReady, persistentHeaders, sessionTenantId]'), 'org-switch retriggers bounded bootstrap')
assert(!app.includes('projectOptions.find((p) => p.id === activeProjectId)'), 'name is not derived from page cache')

{
  const page = {
    items: [
      { id: 'a', name: 'Alpha', status: 'active', updated_at: 't1', file_count: 1 },
      { id: 'b', name: 'Beta', status: 'active', updated_at: 't2', file_count: 0 },
    ],
    next_cursor: 'cursor-2',
    limit: 50,
  }
  const first = applyProjectPage({ items: [] }, page)
  assert(first.items.length === 2, '8: first page renders returned items')
  assert(first.items[0].id === 'a' && first.items[1].id === 'b', 'page order preserved')
  assert(first.nextCursor === 'cursor-2', 'next_cursor kept')
}

{
  const page1 = applyProjectPage({ items: [] }, {
    items: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }],
    next_cursor: 'c2',
  })
  const page2 = applyProjectPage(page1, {
    items: [{ id: 'b', name: 'B-dup' }, { id: 'c', name: 'C' }],
    next_cursor: null,
  })
  assert(page2.items.map((row) => row.id).join(',') === 'a,b,c', '9: load more appends unique ids')
  assert(page2.items.length === 3, 'duplicates from overlap are skipped')
  assert(page2.nextCursor == null, 'terminal cursor')
}

assert(overlay.includes('Load more'), 'Load more control')
assert(overlay.includes("append: true"), 'load more appends')
assert(overlay.includes('projects-row--active'), '10: active project visually marked')
assert(overlay.includes('projectLibraryActiveCopy'), 'active copy uses identity helper')
assert(overlay.includes('projects-row__badge'), 'Active badge')

{
  const page1 = Array.from({ length: 50 }, (_, i) => ({
    id: `p-${i + 1}`,
    name: `Project ${i + 1}`,
  }))
  const project51 = { id: 'p-51', name: 'Project 51' }

  const auto = reconcileActiveProject({ id: null, name: '' }, page1)
  assert(auto.id === 'p-1' && auto.name === 'Project 1', 'A: page 1 open/auto-select keeps id/name')

  const opened = selectActiveProject(project51)
  assert(opened.id === 'p-51', '3: open project 51 sets id')
  assert(opened.name === 'Project 51', 'B: open project 51 sets name')

  const afterRefetch = reconcileActiveProject(opened, page1)
  assert(afterRefetch.id === 'p-51', '4/6: page 1 refetch keeps activeProjectId = 51')
  assert(afterRefetch.name === 'Project 51', '5/7: page 1 refetch keeps activeProjectName')
  assert(
    projectLibraryActiveCopy(afterRefetch) === 'Active project: Project 51',
    'C: subtitle remains truthful'
  )
  assert(
    !projectLibraryActiveCopy(afterRefetch).includes('No project selected'),
    'C: UI does not show No project selected'
  )

  const files = fileLibraryWorkspaceBinding(afterRefetch)
  assert(files.workspaceId === 'p-51', '8: File Library workspace UUID is 51')
  assert(files.workspaceName === 'Project 51', '8: File Library workspace name is 51')
}

{
  const page1 = Array.from({ length: 50 }, (_, i) => ({
    id: `A-${i + 1}`,
    name: `Org A Project ${i + 1}`,
  }))
  let active = selectActiveProject({ id: 'A-51', name: 'Project A51' })
  active = reconcileActiveProject(active, page1)
  assert(active.id === 'A-51' && active.name === 'Project A51', '1: off-page A51 survives page 1 refetch')

  active = clearActiveProject()
  assert(active.id == null && active.name === '', '2: sign-out clears canonical active project')
  assert(projectLibraryActiveCopy(active) === 'No project selected', '2: signed-out copy has no old label')
  assert(fileLibraryWorkspaceBinding(active).workspaceId == null, '2: File Library workspace id cleared')
  assert(fileLibraryWorkspaceBinding(active).workspaceName === '', '2: File Library name cleared')

  const orgBPage1 = [{ id: 'B-1', name: 'Org B First' }, { id: 'B-2', name: 'Org B Second' }]
  const resurrected = reconcileActiveProject(active, orgBPage1)
  assert(resurrected.id !== 'A-51', '3: re-sign-in does not keep A51 id')
  assert(resurrected.name !== 'Project A51', '3: re-sign-in does not keep A51 name')
  assert(resurrected.id === 'B-1' && resurrected.name === 'Org B First', '5: new session auto-selects page 1')
}

assert(app.includes('clearActiveProject(null)'), 'sign-out clears via clearActiveProject')
assert(app.includes('setActiveProject(cleared)'), 'sign-out writes cleared canonical identity')
assert(app.includes('tenantId={sessionTenantId}'), 'overlay reloads when tenant changes')

assert(app.includes('workspaceName={activeProjectName}'), '8: File Library receives independent name')
assert(app.includes('workspaceId={activeProjectId}'), '8: File Library receives active UUID')

assert(app.includes('onOpenProject={handleOpenProject}'), '11: Open Project wired')
assert(app.includes('bindActiveProject(sessionTenantId, project)'), '11: switches workspace id inside tenant')
assert(
  app.includes('workspaceId: persistentReady ? activeProjectId || null : null'),
  '11: inventory follows live tenant-bound activeProjectId'
)

{
  let listed = 0
  const inventory = createWorkspaceFileInventory({
    listFiles: async (workspaceId) => {
      listed += 1
      return {
        items: [{ id: `file-${workspaceId}`, display_name: workspaceId, status: 'ready' }],
      }
    },
    uploadFile: async () => ({}),
  })
  inventory.configure({ workspaceId: 'ws-a', buildHeaders: () => ({ Authorization: 'Bearer a' }) })
  await new Promise((r) => setTimeout(r, 20))
  assert(inventory.getSnapshot().rows.some((row) => row.id === 'file-ws-a'), 'ws-a files present')
  inventory.configure({ workspaceId: 'ws-b', buildHeaders: () => ({ Authorization: 'Bearer a' }) })
  const mid = inventory.getSnapshot()
  assert(mid.files.length === 0, '12: prior project files cleared immediately')
  assert(!mid.rows.some((row) => row.id === 'file-ws-a'), '12: old file ids gone before new fetch settles')
  await new Promise((r) => setTimeout(r, 20))
  assert(inventory.getSnapshot().rows.some((row) => row.id === 'file-ws-b'), 'new project files load after clear')
  assert(listed >= 2, 'workspace list fetched for each workspace')
}

{
  const requested = []
  const inventory = createWorkspaceFileInventory({
    listFiles: async (workspaceId) => {
      requested.push(workspaceId)
      return {
        items: [{ id: `file-${workspaceId}`, display_name: workspaceId, status: 'ready' }],
      }
    },
    uploadFile: async () => ({}),
  })
  inventory.configure({
    workspaceId: 'A-51',
    buildHeaders: () => ({ Authorization: 'Bearer org-a' }),
  })
  await new Promise((r) => setTimeout(r, 20))
  inventory.configure({ workspaceId: null, buildHeaders: null })
  const signedOut = inventory.getSnapshot()
  assert(signedOut.files.length === 0, '4: inventory cleared while signed out')
  assert(signedOut.rows.length === 0, '4: no signed-out file rows')
  assert(signedOut.workspaceId == null, '4: inventory workspace id is null')
  inventory.configure({
    workspaceId: 'B-1',
    buildHeaders: () => ({ Authorization: 'Bearer org-b' }),
  })
  await new Promise((r) => setTimeout(r, 20))
  assert(!requested.includes('A-51') || requested[requested.length - 1] === 'B-1', '3: latest list is new session')
  assert(
    !inventory.getSnapshot().rows.some((row) => row.id === 'file-A-51'),
    '3: A51 workspace files are not retained after new session'
  )
  assert(
    inventory.getSnapshot().rows.some((row) => row.id === 'file-B-1'),
    '5: new authenticated workspace files load'
  )
  assert(!requested.slice(1).includes('A-51'), '3: no A51 workspace request after sign-out')
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

{
  const orgA = resolveActiveTenantId({
    clerkEnabled: true,
    isSignedIn: true,
    orgId: 'org_A',
    userId: 'user_1',
  })
  const orgB = resolveActiveTenantId({
    clerkEnabled: true,
    isSignedIn: true,
    orgId: 'org_B',
    userId: 'user_1',
  })
  const personal = resolveActiveTenantId({
    clerkEnabled: true,
    isSignedIn: true,
    orgId: null,
    userId: 'user_1',
  })
  assert(orgA === 'org:org_A' && isOrganizationTenantId(orgA), 'tenant source: Clerk orgId → org tenant')
  assert(orgB === 'org:org_B', 'company A and company B are distinct tenants')
  assert(personal === 'personal:user_1' && isPersonalTenantId(personal), 'no orgId → personal tenant')
  assert(
    resolveActiveTenantId({ clerkEnabled: true, isSignedIn: false, orgId: 'org_A', userId: 'user_1' }) == null,
    'signed out has no tenant'
  )
  assert(
    isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: true }),
    '3: persistentReady stays true across org switch (orgId is not part of session-ready)'
  )

  const aPage1 = Array.from({ length: 50 }, (_, i) => ({
    id: `A-${i + 1}`,
    name: `Org A Project ${i + 1}`,
  }))
  const persistentReady = true
  let jwt = 'Bearer org-a'
  let tenantId = orgA
  let active = bindActiveProject(tenantId, { id: 'A-51', name: 'Project A51' })
  active = reconcileActiveProject(active, aPage1, tenantId)
  assert(active.id === 'A-51' && active.name === 'Project A51', '1: Org A active project A51')
  assert(active.tenantId === orgA, 'A51 is bound to org A')

  const sameTenantRefetch = reconcileActiveProject(active, aPage1, tenantId)
  assert(sameTenantRefetch.id === 'A-51', 'off-page A51 survives page-1 refetch within SAME tenant')
  assert(sameTenantRefetch.name === 'Project A51', 'same-tenant refetch keeps A51 name')

  const requested = []
  const inventory = createWorkspaceFileInventory({
    listFiles: async (workspaceId, headers) => {
      requested.push({ workspaceId, jwt: headers?.Authorization || null })
      return {
        items: [{ id: `file-${workspaceId}`, display_name: workspaceId, status: 'ready' }],
      }
    },
    uploadFile: async () => ({}),
  })

  let binding = workspaceBindingForSession({ persistentReady, tenantId, active })
  inventory.configure({
    workspaceId: binding.workspaceId,
    buildHeaders: async () => ({ Authorization: jwt }),
  })
  await sleep(20)
  assert(
    requested.some((r) => r.workspaceId === 'A-51' && r.jwt === 'Bearer org-a'),
    'A51 listed under org A JWT'
  )

  jwt = 'Bearer org-b'
  tenantId = orgB
  const switched = applyTenantScopeChange(active, tenantId)
  assert(switched.changed, '2: org A → org B is a tenant change')
  active = { tenantId: switched.tenantId, id: switched.id, name: switched.name }
  assert(
    isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: true }),
    '3: still signed in after org switch'
  )
  binding = workspaceBindingForSession({ persistentReady, tenantId, active })
  assert(active.id == null && active.name === '', '4: activeProject becomes null before B workspace load')
  assert(binding.workspaceId == null, '4: workspace binding is null before B load')
  assert(projectLibraryActiveCopy(active) === 'No project selected', '8: UI never shows A51 as active under B')
  assert(projectLibraryActiveCopy(active).includes('A51') === false, '8: A51 label gone')
  inventory.configure({
    workspaceId: binding.workspaceId,
    buildHeaders: async () => ({ Authorization: jwt }),
  })
  await sleep(20)
  assert(
    !requested.some((r) => r.workspaceId === 'A-51' && r.jwt === 'Bearer org-b'),
    '5: no request for A51 is sent with B JWT'
  )
  assert(inventory.getSnapshot().workspaceId == null, '2: inventory disabled for old project')
  assert(inventory.getSnapshot().files.length === 0, '2: old workspace files cleared')

  const bPage = [{ id: 'B-1', name: 'Org B First' }, { id: 'B-2', name: 'Org B Second' }]
  active = reconcileActiveProject(active, bPage, tenantId)
  assert(active.id === 'B-1' && active.name === 'Org B First', '6/7: B project list selects B project only')
  assert(active.tenantId === orgB, '7: B project is bound to org B')
  assert(active.id !== 'A-51', '7: A51 is not active under B')
  binding = workspaceBindingForSession({ persistentReady, tenantId, active })
  inventory.configure({
    workspaceId: binding.workspaceId,
    buildHeaders: async () => ({ Authorization: jwt }),
  })
  await sleep(20)
  assert(
    requested.some((r) => r.workspaceId === 'B-1' && r.jwt === 'Bearer org-b'),
    '6: B workspace loads under B JWT'
  )
  assert(projectLibraryActiveCopy(active) === 'Active project: Org B First', '8: UI shows B, not A51')
}

{
  const company = resolveActiveTenantId({
    clerkEnabled: true,
    isSignedIn: true,
    orgId: 'org_A',
    userId: 'user_1',
  })
  const personal = resolveActiveTenantId({
    clerkEnabled: true,
    isSignedIn: true,
    orgId: null,
    userId: 'user_1',
  })
  let active = bindActiveProject(company, { id: 'A-1', name: 'Company Project' })
  let next = applyTenantScopeChange(active, personal)
  active = { tenantId: next.tenantId, id: next.id, name: next.name }
  assert(active.id == null, 'Company → Personal clears active project')
  assert(projectLibraryActiveCopy(active) === 'No project selected', 'Company → Personal drops company label')
  const personalPage = [{ id: 'P-1', name: 'Personal First' }]
  active = reconcileActiveProject(active, personalPage, personal)
  assert(active.id === 'P-1' && active.tenantId === personal, 'Personal bootstrap selects personal project')

  next = applyTenantScopeChange(active, company)
  active = { tenantId: next.tenantId, id: next.id, name: next.name }
  assert(active.id == null, 'Personal → Company clears personal project')
  assert(active.name === '', 'Personal → Company does not keep personal name')
  const companyPage = [{ id: 'A-9', name: 'Company Nine' }]
  active = reconcileActiveProject(active, companyPage, company)
  assert(active.id === 'A-9' && active.tenantId === company, 'Company bootstrap selects company project')
  assert(active.id !== 'P-1', 'personal project is not active under company')
}

{
  const tenantId = resolveActiveTenantId({
    clerkEnabled: true,
    isSignedIn: true,
    orgId: 'org_A',
    userId: 'user_1',
  })
  let active = bindActiveProject(tenantId, { id: 'A-51', name: 'Project A51' })
  const liveSignedOut = workspaceBindingForSession({
    persistentReady: false,
    tenantId,
    active,
  })
  assert(liveSignedOut.workspaceId == null, 'sign-out still clears workspace binding while tenant key exists')
  active = clearActiveProject(null)
  assert(active.id == null && active.tenantId == null, 'sign-out still clears canonical active project')
  assert(projectLibraryActiveCopy(active) === 'No project selected', 'sign-out copy has no old label')
}

{
  const mismatched = activeProjectForTenant(
    bindActiveProject('org:org_A', { id: 'A-51', name: 'Project A51' }),
    'org:org_B'
  )
  assert(mismatched.id == null && mismatched.name === '', 'mismatched tenant rejects stored project')
  assert(
    fileLibraryWorkspaceBinding(
      bindActiveProject('org:org_A', { id: 'A-51', name: 'Project A51' }),
      'org:org_B'
    ).workspaceId == null,
    'File Library does not keep A51 under B'
  )
}

assert(overlay.includes('tenantId'), 'overlay receives tenant identity')
assert(overlay.includes('[open, tenantId]'), 'overlay reloads page 1 on tenant change')

assert(app.includes('<NewProjectModal'), '13: New Project modal remains')
assert(overlay.includes('onNewProject'), '13: library reuses New Project')
assert(modal.includes('Create new project') || modal.includes('+ New Project'), '13: existing modal')
assert(app.includes('setNewProjectModalOpen(true)'), '13: library opens existing modal')

assert(
  projectLibraryEmptyMessage({ signedIn: true, loading: false, error: null, itemCount: 0 }) ===
    PROJECT_LIBRARY_EMPTY.inventoryEmpty,
  '14: empty state truthful'
)
assert(
  projectLibraryEmptyMessage({ signedIn: true, loading: true, itemCount: 0 }) == null,
  '14: loading is not empty'
)
assert(overlay.includes('Loading projects…'), '15: loading copy')
assert(overlay.includes('Could not load projects.'), '15: error copy')
assert(overlay.includes('Retry'), '15: retry on error')

assert(overlay.includes('fetchProjects('), 'single list API')
assert(!overlay.includes('/files'), '7: no per-project file fetches')
assert(!overlay.includes('fetchProject('), '7: no per-project detail fetches')
assert(api.includes("params.set('limit'"), 'bounded limit query param')
assert(api.includes("params.set('cursor'"), 'cursor continuation')

const merged = mergeProjectPage([{ id: '1' }], [{ id: '1' }, { id: '2' }])
assert(merged.length === 2, 'merge unique')

assert(inventorySrc.includes('files = []'), '16: inventory still clears on scope change')
assert(app.includes('workspaceFileInventory'), '16: shared inventory unchanged')

assert(!app.includes('BEN_WORKSPACE_CHUNK_RETRIEVAL'), '18: frontend does not enable Gate 4A')

console.log('PASS: project library V1 UI')
