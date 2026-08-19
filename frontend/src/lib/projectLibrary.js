/**
 * Project Library V1 — presentation helpers.
 * Bounded in-memory pages only. Does not fetch per-project details.
 */

export const PROJECT_LIBRARY_DEFAULT_LIMIT = 50
export const PROJECT_LIBRARY_MAX_ITEMS = 1000
export const PROJECT_LIBRARY_REOPEN_RESETS = true

export const PROJECT_LIBRARY_EMPTY = {
  signedOut: 'Sign in to browse projects.',
  inventoryEmpty: 'No projects yet. Create a project to get started.',
  noMatches: 'No more projects to load.',
}

export function projectLibraryEmptyMessage({
  signedIn = true,
  loading = false,
  error = null,
  itemCount = 0,
} = {}) {
  if (!signedIn) return PROJECT_LIBRARY_EMPTY.signedOut
  if (loading) return null
  if (error) return null
  if (Number(itemCount) > 0) return null
  return PROJECT_LIBRARY_EMPTY.inventoryEmpty
}

export function mergeProjectPage(existing, incoming) {
  const list = Array.isArray(existing) ? existing.slice() : []
  const seen = new Set(list.map((row) => String(row?.id || '')))
  for (const row of Array.isArray(incoming) ? incoming : []) {
    const id = String(row?.id || '')
    if (!id || seen.has(id)) continue
    seen.add(id)
    list.push(row)
  }
  return list
}

export function applyProjectPage(state, page, { maxItems = PROJECT_LIBRARY_MAX_ITEMS } = {}) {
  const incoming = Array.isArray(page?.items)
    ? page.items
    : Array.isArray(page?.projects)
      ? page.projects
      : []
  const merged = mergeProjectPage(state?.items || [], incoming)
  const bounded = merged.slice(0, Math.max(1, Number(maxItems) || PROJECT_LIBRARY_MAX_ITEMS))
  const capped = bounded.length < merged.length
  return {
    items: bounded,
    nextCursor: capped ? null : page?.next_cursor || null,
    limit: Number(page?.limit) || state?.limit || PROJECT_LIBRARY_DEFAULT_LIMIT,
  }
}
