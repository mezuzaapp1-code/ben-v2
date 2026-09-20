/**
 * Ordinary + New chat must not send project_id.
 * Run: node frontend/scripts/test-clean-chat-isolation.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  bindActiveProject,
  chatStreamProjectId,
  clearActiveProject,
  reconcileActiveProject,
} from '../src/lib/activeProject.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

function outgoingChatStream({ message, threadId, activeProjectId }) {
  const body = { message, tier: 'free' }
  if (threadId) body.thread_id = threadId
  const projectId = chatStreamProjectId(activeProjectId)
  if (projectId) body.project_id = projectId
  return body
}

const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
const workspace = readFileSync(join(root, 'src/components/ProjectWorkspacePanel.jsx'), 'utf8')
const chatApi = readFileSync(join(root, 'src/api/chat.js'), 'utf8')

assert(app.includes('startOrdinaryNewChat'), '1: named + New chat handler')
assert(app.includes('onClick={startOrdinaryNewChat}'), '1: + New chat uses detach handler')
assert(
  app.includes('setActiveProject(clearActiveProject(sessionTenantId))'),
  '1: + New chat clears activeProjectId'
)
assert(app.includes('projectId: chatStreamProjectId(activeProjectId)'), '1: stream uses explicit project helper')

{
  const tenantId = 'org:org_A'
  const projects = [
    { id: 'p-1', name: 'Alpha' },
    { id: 'p-2', name: 'Beta' },
  ]
  let active = bindActiveProject(tenantId, projects[0])
  assert(active.id === 'p-1', 'setup: project is bound')

  active = clearActiveProject(tenantId)
  assert(active.id == null, '1: + New chat sets activeProjectId null')
  assert(active.tenantId === tenantId, '1: tenant is preserved after detach')

  const body = outgoingChatStream({
    message: 'What is the capital of France? Answer in one sentence.',
    threadId: 'draft-1',
    activeProjectId: active.id,
  })
  assert(!Object.prototype.hasOwnProperty.call(body, 'project_id'), '1: outgoing /chat/stream has no project_id')
}

{
  const tenantId = 'org:org_A'
  const existing = [
    { id: 'p-1', name: 'First available' },
    { id: 'p-2', name: 'Second' },
  ]
  const afterList = reconcileActiveProject(clearActiveProject(tenantId), existing, tenantId)
  assert(afterList.id == null, '2: existing projects do not auto-bind ordinary New chat')
  const body = outgoingChatStream({
    message: 'hello',
    activeProjectId: afterList.id,
  })
  assert(!Object.prototype.hasOwnProperty.call(body, 'project_id'), '2: stream omits project_id when projects exist')
}

{
  const tenantId = 'org:org_A'
  const opened = bindActiveProject(tenantId, { id: 'p-99', name: 'Explicit' })
  assert(opened.id === 'p-99', '3: opening a project binds id')
  const kept = reconcileActiveProject(opened, [{ id: 'p-1', name: 'Other' }], tenantId)
  assert(kept.id === 'p-99', '3: explicit project survives list refetch')
  const body = outgoingChatStream({
    message: 'quote this jobsite in Shoham',
    activeProjectId: kept.id,
  })
  assert(body.project_id === 'p-99', '3: explicit project chat still sends project_id')
}

assert(workspace.includes('if (!activeProjectId && list[0]?.id)'), '7: project workspace still auto-selects')
assert(workspace.includes('onProjectChange(list[0].id)'), '7: workspace auto-select stays inside panel')
assert(!app.includes('<ProjectWorkspacePanel'), '7: workspace auto-select is not wired to ordinary composer')
assert(app.includes('handleOpenProject'), '7: explicit open project handler remains')
assert(app.includes('bindActiveProject(sessionTenantId, project)'), '7: open project still binds identity')
assert(chatApi.includes('buildChatStreamRequestBody'), 'stream uses shared body builder')
assert(chatApi.includes('if (projectId) body.project_id = projectId'), 'stream body still gates project_id')
assert(chatApi.includes("gateAMark('F1_fetch'"), 'Gate A frontend fetch mark preserved')

console.log('PASS: clean chat isolation')
