/** UI copy keyed off browser / document language (en | he). */
export function getUiLocale() {
  try {
    const stored = localStorage.getItem('ben-ui-locale')
    if (stored === 'he' || stored === 'en') return stored
  } catch {
    /* ignore */
  }
  const raw = (
    document.documentElement.lang ||
    (typeof navigator !== 'undefined' ? navigator.language : '') ||
    'en'
  ).toLowerCase()
  return raw.startsWith('he') ? 'he' : 'en'
}

/** Persist presentation locale and notify listeners (News EN / עברית). */
export function setUiLocale(next) {
  const locale = next === 'he' ? 'he' : 'en'
  try {
    localStorage.setItem('ben-ui-locale', locale)
  } catch {
    /* ignore */
  }
  try {
    document.documentElement.lang = locale
  } catch {
    /* ignore */
  }
  try {
    window.dispatchEvent(new CustomEvent('ben-ui-locale', { detail: { locale } }))
  } catch {
    /* ignore */
  }
  return locale
}

export function newsOriginalEnLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'מקור (EN)' : 'Original (EN)'
}

export function newsLocaleEnLabel() {
  return 'English'
}

export function newsLocaleHeLabel() {
  return 'עברית'
}

export function promoteToProjectLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'הפוך לפרויקט' : 'Promote to Project'
}

export function deleteConversationLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'מחק' : 'Delete'
}

export function deletingConversationLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'מוחק…' : 'Deleting…'
}

export function deleteConversationAriaLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'מחק שיחה' : 'Delete conversation'
}

export function deleteConversationTitle(locale = getUiLocale()) {
  return locale === 'he' ? 'מחק את סביבת העבודה' : 'Delete this workspace'
}

export function historySectionTitle(totalVisibleCount, locale = getUiLocale()) {
  if (locale === 'he') return `היסטוריה (${totalVisibleCount})`
  return `History (${totalVisibleCount})`
}

export function historySelectionTitle(selectedCount, totalVisibleCount, locale = getUiLocale()) {
  if (locale === 'he') return `נבחרו ${selectedCount} מתוך ${totalVisibleCount}`
  return `Selected ${selectedCount} of ${totalVisibleCount}`
}

export function selectedCountLabel(count, locale = getUiLocale()) {
  if (locale === 'he') return `סומנו ${count} שיחות`
  return `${count} selected`
}

export function selectAllLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'בחר הכל' : 'Select All'
}

export function deselectAllLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'בטל בחירה' : 'Deselect All'
}

export function deleteSelectedLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'מחק פריטים שסומנו' : 'Delete Selected'
}

export function bulkDeleteConfirmMessage(count, locale = getUiLocale()) {
  if (locale === 'he') {
    return `האם אתה בטוח שברצונך למחוק את ${count} השיחות שנבחרו ואת כל קבצי הפרויקט שלהן לצמיתות?`
  }
  return `Are you sure you want to permanently delete the ${count} selected conversations and all their associated project files?`
}

export function singleDeleteConfirmMessage(locale = getUiLocale()) {
  if (locale === 'he') {
    return 'למחוק את סביבת העבודה הזו ואת כל קבצי הפרויקט הפיזיים הקשורים אליה?'
  }
  return 'Delete this workspace and all associated physical project files?'
}

/** BEN News chrome (content language stays English until translation pass). */
export function newsEyebrowLabel(locale = getUiLocale()) {
  return 'BEN News'
}

export function newsFeedTitle(locale = getUiLocale()) {
  return locale === 'he' ? '10 החדשות המובילות' : 'Top 10 AI News'
}

export function newsTopicTitle(locale = getUiLocale()) {
  return locale === 'he' ? 'נושא' : 'Topic'
}

export function newsCloseLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'סגור' : 'Close'
}

export function newsCloseNewsLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'סגור את BEN News' : 'Close BEN News'
}

export function newsLoadingFeedLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'טוען ידיעות…' : 'Loading top stories…'
}

export function newsLoadingTopicLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'טוען נושא…' : 'Loading topic…'
}

export function newsRetryLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'נסה שוב' : 'Retry'
}

export function newsEmptyTitle(locale = getUiLocale()) {
  return locale === 'he' ? 'עדיין אין ידיעות מדורגות.' : 'No ranked news topics are available yet.'
}

export function newsEmptyHint(locale = getUiLocale()) {
  return locale === 'he'
    ? 'ידיעות חדשות יופיעו אחרי מחזור האיסוף והבנייה הבא.'
    : 'New stories will appear after the next collection and build cycle.'
}

export function newsBackToFeedLabel(locale = getUiLocale()) {
  return locale === 'he' ? '← חזרה ל־Top 10' : '← Back to Top 10'
}

export function newsTopicMissingLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'הנושא הזה כבר לא זמין.' : 'This news topic is no longer available.'
}

export function newsSourceCountLabel(count, locale = getUiLocale()) {
  const n = Number(count) || 0
  if (locale === 'he') return n === 1 ? 'מקור אחד' : `${n} מקורות`
  return n === 1 ? '1 source' : `${n} sources`
}

export function newsArticleCountLabel(count, locale = getUiLocale()) {
  const n = Number(count) || 0
  if (locale === 'he') return n === 1 ? 'מאמר אחד' : `${n} מאמרים`
  return n === 1 ? '1 article' : `${n} articles`
}

export function newsOpenConflictLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'מחלוקת פתוחה' : 'Open conflict'
}

export function newsWhyHeading(locale = getUiLocale()) {
  return locale === 'he' ? 'למה זה חשוב' : 'Why it matters'
}

export function newsCoverageHeading(locale = getUiLocale()) {
  return locale === 'he' ? 'סיקור תומך' : 'Supporting coverage'
}

export function newsSourcesHeading(locale = getUiLocale()) {
  return locale === 'he' ? 'מקורות' : 'Sources'
}

export function newsFactsHeading(locale = getUiLocale()) {
  return locale === 'he' ? 'עובדות נוכחיות' : 'Current facts'
}

export function newsConflictsHeading(locale = getUiLocale()) {
  return locale === 'he' ? 'מחלוקות' : 'Conflicts'
}

export function newsOpensInNewTabLabel(locale = getUiLocale()) {
  return locale === 'he' ? ' (נפתח בלשונית חדשה)' : ' (opens in new tab)'
}

export function newsDefaultSourceLabel(locale = getUiLocale()) {
  return locale === 'he' ? 'מקור' : 'Source'
}

export function newsImageAlt(headline, locale = getUiLocale()) {
  const title = String(headline || '').trim()
  if (locale === 'he') return title ? `תמונה: ${title}` : 'תמונת הידיעה'
  return title ? `Image: ${title}` : 'Story image'
}
