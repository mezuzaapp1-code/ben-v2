/**
 * Canonical active project identity.
 *
 * Project Library pages are browsing/cache only. Opening a project writes
 * this identity. Refetching page 1 must not drop an off-page active project.
 * Sign-out / persistentReady=false must clear this identity so the next
 * session cannot inherit the previous tenant's project.
 */
export function selectActiveProject(project) {
  const id = String(project?.id || '').trim()
  if (!id) return { id: null, name: '' }
  const name = String(project?.name || '').trim() || 'Project'
  return { id, name }
}

export function clearActiveProject() {
  return { id: null, name: '' }
}

export function reconcileActiveProject(active, pageRows) {
  const rows = Array.isArray(pageRows) ? pageRows : []
  const currentId = String(active?.id || '').trim()
  const currentName = String(active?.name || '').trim()

  if (!currentId) {
    const first = rows[0]
    if (!first?.id) return { id: null, name: '' }
    return selectActiveProject(first)
  }

  const match = rows.find((row) => String(row?.id || '') === currentId)
  if (match) {
    return {
      id: currentId,
      name: String(match.name || currentName || '').trim() || 'Project',
    }
  }
  return { id: currentId, name: currentName }
}

export function fileLibraryWorkspaceBinding(active) {
  const id = String(active?.id || '').trim() || null
  const name = String(active?.name || '').trim()
  return { workspaceId: id, workspaceName: name }
}

export function projectLibraryActiveCopy(active) {
  const id = String(active?.id || '').trim()
  const name = String(active?.name || '').trim()
  if (name) return `Active project: ${name}`
  if (id) return 'Active project'
  return 'No project selected'
}
