/**
 * Compact relative times for product News UI (no extra date library).
 */

/**
 * @param {string | Date | null | undefined} value
 * @param {Date} [now]
 * @param {'en'|'he'} [locale]
 * @returns {{ label: string, absolute: string } | null}
 */
export function formatRelativeTime(value, now = new Date(), locale = 'en') {
  if (value == null || value === '') return null
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const he = locale === 'he'

  const absolute = new Intl.DateTimeFormat(he ? 'he-IL' : undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)

  const diffMs = now.getTime() - date.getTime()
  const past = diffMs >= 0
  const absSec = Math.round(Math.abs(diffMs) / 1000)

  let short
  if (absSec < 45) short = past ? (he ? 'עכשיו' : 'just now') : he ? 'בעוד רגע' : 'in a moment'
  else if (absSec < 3600) {
    const m = Math.max(1, Math.round(absSec / 60))
    short = past ? (he ? `לפני ${m} דק׳` : `${m}m ago`) : he ? `בעוד ${m} דק׳` : `in ${m}m`
  } else if (absSec < 86400) {
    const h = Math.max(1, Math.round(absSec / 3600))
    short = past ? (he ? `לפני ${h} שע׳` : `${h}h ago`) : he ? `בעוד ${h} שע׳` : `in ${h}h`
  } else if (absSec < 86400 * 2) {
    short = past ? (he ? 'אתמול' : 'yesterday') : he ? 'מחר' : 'tomorrow'
  } else if (absSec < 86400 * 30) {
    const d = Math.max(2, Math.round(absSec / 86400))
    short = past ? (he ? `לפני ${d} ימים` : `${d}d ago`) : he ? `בעוד ${d} ימים` : `in ${d}d`
  } else {
    short = absolute
  }

  return { label: short, absolute }
}

/**
 * @param {string | Date | null | undefined} value
 * @param {Date} [now]
 * @param {'en'|'he'} [locale]
 * @returns {{ label: string, absolute: string } | null}
 */
export function formatUpdatedLabel(value, now = new Date(), locale = 'en') {
  const he = locale === 'he'
  const rel = formatRelativeTime(value, now, locale)
  if (!rel) return null
  if (rel.label === rel.absolute) {
    return { label: he ? `עודכן ${rel.absolute}` : `Updated ${rel.absolute}`, absolute: rel.absolute }
  }
  if (rel.label === 'just now' || rel.label === 'עכשיו') {
    return { label: he ? 'עודכן עכשיו' : 'Updated just now', absolute: rel.absolute }
  }
  if (rel.label === 'yesterday' || rel.label === 'אתמול') {
    return { label: he ? 'עודכן אתמול' : 'Updated yesterday', absolute: rel.absolute }
  }
  if (
    rel.label.startsWith('in ') ||
    rel.label.startsWith('בעוד ') ||
    rel.label === 'tomorrow' ||
    rel.label === 'מחר' ||
    rel.label === 'in a moment' ||
    rel.label === 'בעוד רגע'
  ) {
    return { label: rel.label, absolute: rel.absolute }
  }
  return { label: he ? `עודכן ${rel.label}` : `Updated ${rel.label}`, absolute: rel.absolute }
}
