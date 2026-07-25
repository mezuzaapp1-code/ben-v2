/**
 * Compact relative times for product News UI (no extra date library).
 */

/**
 * @param {string | Date | null | undefined} value
 * @param {Date} [now]
 * @returns {{ label: string, absolute: string } | null}
 */
export function formatRelativeTime(value, now = new Date()) {
  if (value == null || value === '') return null
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return null

  const absolute = new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)

  const diffMs = now.getTime() - date.getTime()
  const past = diffMs >= 0
  const absSec = Math.round(Math.abs(diffMs) / 1000)

  let short
  if (absSec < 45) short = past ? 'just now' : 'in a moment'
  else if (absSec < 3600) {
    const m = Math.max(1, Math.round(absSec / 60))
    short = past ? `${m}m ago` : `in ${m}m`
  } else if (absSec < 86400) {
    const h = Math.max(1, Math.round(absSec / 3600))
    short = past ? `${h}h ago` : `in ${h}h`
  } else if (absSec < 86400 * 2) {
    short = past ? 'yesterday' : 'tomorrow'
  } else if (absSec < 86400 * 30) {
    const d = Math.max(2, Math.round(absSec / 86400))
    short = past ? `${d}d ago` : `in ${d}d`
  } else {
    short = absolute
  }

  return { label: short, absolute }
}

/**
 * @param {string | Date | null | undefined} value
 * @param {Date} [now]
 * @returns {{ label: string, absolute: string } | null}
 */
export function formatUpdatedLabel(value, now = new Date()) {
  const rel = formatRelativeTime(value, now)
  if (!rel) return null
  if (rel.label === rel.absolute) {
    return { label: `Updated ${rel.absolute}`, absolute: rel.absolute }
  }
  if (rel.label === 'just now') {
    return { label: 'Updated just now', absolute: rel.absolute }
  }
  if (rel.label === 'yesterday') {
    return { label: 'Updated yesterday', absolute: rel.absolute }
  }
  if (rel.label.startsWith('in ') || rel.label === 'tomorrow' || rel.label === 'in a moment') {
    return { label: rel.label, absolute: rel.absolute }
  }
  return { label: `Updated ${rel.label}`, absolute: rel.absolute }
}
