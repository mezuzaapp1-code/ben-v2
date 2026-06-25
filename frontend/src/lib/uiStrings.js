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
