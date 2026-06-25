/** Normalize portable project slugs for workspace thread filtering. */
export function normalizeProjectSlug(slug) {
  return (
    String(slug || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9\-_]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64) || ''
  )
}

/**
 * History list scoped to the active project workspace.
 * When a project slug is active, only matching portfolio threads are shown.
 * Otherwise general chats (no project slug) are shown.
 */
export function filterThreadsForWorkspace(threads, activeProjectSlug) {
  const activeSlug = normalizeProjectSlug(activeProjectSlug)
  if (activeSlug) {
    return (threads || []).filter(
      (thread) => normalizeProjectSlug(thread.projectSlug) === activeSlug
    )
  }
  return (threads || []).filter((thread) => !normalizeProjectSlug(thread.projectSlug))
}
