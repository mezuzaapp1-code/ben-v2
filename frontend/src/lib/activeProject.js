/**
 * Canonical active project identity.
 *
 * Project Library pages are browsing/cache only. Opening a project writes
 * this identity. Refetching page 1 must not drop an off-page active project
 * inside the same tenant.
 *
 * An active project is valid only inside the exact current tenant/security
 * context. Sign-out and tenant/org change must clear this identity so a
 * later session or org cannot inherit the previous tenant's project.
 */
import { resolveActiveTenantId } from './tenantIdentity.js'

export function selectActiveProject(project) {
  const id = String(project?.id || '').trim()
  if (!id) return { id: null, name: '' }
  const name = String(project?.name || '').trim() || 'Project'
  return { id, name }
}

export function clearActiveProject(tenantId = null) {
  return { tenantId: tenantId || null, id: null, name: '' }
}

export function bindActiveProject(tenantId, project) {
  const tid = String(tenantId || '').trim() || null
  if (!tid) return clearActiveProject(null)
  const selected = selectActiveProject(project)
  if (!selected.id) return clearActiveProject(tid)
  return { tenantId: tid, id: selected.id, name: selected.name }
}

export function applyTenantScopeChange(active, nextTenantId) {
  const next = String(nextTenantId || '').trim() || null
  const prev = String(active?.tenantId || '').trim() || null
  if (prev === next) {
    return {
      tenantId: prev,
      id: String(active?.id || '').trim() || null,
      name: String(active?.name || '').trim(),
      changed: false,
    }
  }
  return { tenantId: next, id: null, name: '', changed: true }
}

/**
 * Reject a stored active project that does not belong to tenantId.
 * When tenantId is omitted, keep current {id,name} (same-tenant helpers).
 */
export function activeProjectForTenant(active, tenantId) {
  if (arguments.length < 2) {
    return {
      tenantId: String(active?.tenantId || '').trim() || null,
      id: String(active?.id || '').trim() || null,
      name: String(active?.name || '').trim(),
    }
  }
  const current = String(tenantId || '').trim() || null
  if (!current) return clearActiveProject(null)
  const bound = String(active?.tenantId || '').trim() || null
  const id = String(active?.id || '').trim() || null
  const name = String(active?.name || '').trim()
  if (bound !== current) return clearActiveProject(current)
  return { tenantId: current, id, name }
}

export function reconcileActiveProject(active, pageRows, tenantId) {
  const tenantScoped = arguments.length >= 3
  const scoped = tenantScoped
    ? activeProjectForTenant(active, tenantId)
    : {
        tenantId: String(active?.tenantId || '').trim() || null,
        id: String(active?.id || '').trim() || null,
        name: String(active?.name || '').trim(),
      }
  const rows = Array.isArray(pageRows) ? pageRows : []
  const currentId = String(scoped?.id || '').trim()
  const currentName = String(scoped?.name || '').trim()
  const boundTenant = tenantScoped
    ? String(tenantId || '').trim() || null
    : scoped.tenantId

  if (!currentId) {
    const first = rows[0]
    if (!first?.id) return { tenantId: boundTenant, id: null, name: '' }
    if (boundTenant) return bindActiveProject(boundTenant, first)
    const selected = selectActiveProject(first)
    return { tenantId: null, id: selected.id, name: selected.name }
  }

  const match = rows.find((row) => String(row?.id || '') === currentId)
  if (match) {
    const name = String(match.name || currentName || '').trim() || 'Project'
    if (boundTenant) return bindActiveProject(boundTenant, { id: currentId, name })
    return { tenantId: null, id: currentId, name }
  }
  return {
    tenantId: boundTenant,
    id: currentId,
    name: currentName,
  }
}

export function fileLibraryWorkspaceBinding(active, tenantId) {
  const live =
    arguments.length < 2 ? activeProjectForTenant(active) : activeProjectForTenant(active, tenantId)
  const id = String(live?.id || '').trim() || null
  const name = String(live?.name || '').trim()
  return { workspaceId: id, workspaceName: name }
}

export function workspaceBindingForSession({ persistentReady = false, tenantId = null, active = null } = {}) {
  if (!persistentReady) {
    return { workspaceId: null, workspaceName: '', tenantId: null }
  }
  const live = activeProjectForTenant(active, tenantId)
  return {
    workspaceId: live.id,
    workspaceName: live.name,
    tenantId: live.tenantId,
  }
}

export function projectLibraryActiveCopy(active) {
  const id = String(active?.id || '').trim()
  const name = String(active?.name || '').trim()
  if (name) return `Active project: ${name}`
  if (id) return 'Active project'
  return 'No project selected'
}

export { resolveActiveTenantId }
