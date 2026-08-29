import { forwardRef, useCallback, useEffect, useMemo, useState } from 'react'
import {
  bulkDeleteConfirmMessage,
  deleteSelectedLabel,
  deselectAllLabel,
  historySectionTitle,
  historySelectionTitle,
  selectAllLabel,
} from '../lib/uiStrings.js'
import { filterThreadsForWorkspace } from '../lib/threadWorkspace.js'
import { useUiLocale } from '../hooks/useUiLocale.js'
import './NavDrawer.css'

export function NavDrawerHistory({
  threads = [],
  activeProjectSlug = null,
  activeId,
  onSelectThread,
  onBulkDelete,
  disabled = false,
}) {
  const [selectedThreadIds, setSelectedThreadIds] = useState([])
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const locale = useUiLocale()

  const visibleThreads = useMemo(
    () => filterThreadsForWorkspace(threads, activeProjectSlug),
    [threads, activeProjectSlug]
  )
  const visibleThreadIds = useMemo(
    () => visibleThreads.map((thread) => thread.id),
    [visibleThreads]
  )

  const hasSelection = selectedThreadIds.length > 0
  const allVisibleSelected =
    visibleThreadIds.length > 0 &&
    visibleThreadIds.every((threadId) => selectedThreadIds.includes(threadId))

  const totalVisibleCount = visibleThreads.length
  const selectedCount = selectedThreadIds.length
  const historyTitle = hasSelection
    ? historySelectionTitle(selectedCount, totalVisibleCount, locale)
    : historySectionTitle(totalVisibleCount, locale)

  useEffect(() => {
    setSelectedThreadIds((prev) => prev.filter((id) => visibleThreadIds.includes(id)))
  }, [visibleThreadIds])

  const toggleThreadSelection = useCallback((threadId, checked) => {
    setSelectedThreadIds((prev) => {
      if (checked) return prev.includes(threadId) ? prev : [...prev, threadId]
      return prev.filter((id) => id !== threadId)
    })
  }, [])

  const clearSelection = useCallback(() => {
    setSelectedThreadIds([])
  }, [])

  const toggleSelectAll = useCallback(() => {
    setSelectedThreadIds((prev) => {
      const allSelected =
        visibleThreadIds.length > 0 &&
        visibleThreadIds.every((threadId) => prev.includes(threadId))
      if (allSelected) {
        return prev.filter((id) => !visibleThreadIds.includes(id))
      }
      const next = new Set(prev)
      for (const threadId of visibleThreadIds) next.add(threadId)
      return [...next]
    })
  }, [visibleThreadIds])

  const handleDeleteSelected = useCallback(async () => {
    if (!selectedThreadIds.length || bulkDeleting || disabled) return
    const idsToDelete = [...selectedThreadIds]
    const count = idsToDelete.length
    const confirmed = window.confirm(bulkDeleteConfirmMessage(count, locale))
    if (!confirmed) return

    setBulkDeleting(true)
    setSelectedThreadIds([])
    try {
      await onBulkDelete?.(idsToDelete)
    } finally {
      setBulkDeleting(false)
    }
  }, [bulkDeleting, disabled, locale, onBulkDelete, selectedThreadIds])

  return (
    <section
      className={`nav-drawer__section nav-drawer__section--history${hasSelection ? ' nav-drawer__section--selecting' : ''}`}
    >
      <div className="nav-drawer__history-header">
        <h2 className="nav-drawer__section-title nav-drawer__history-title">{historyTitle}</h2>
        {totalVisibleCount > 0 ? (
          <label className="nav-drawer__select-all" title={selectAllLabel(locale)}>
            <input
              type="checkbox"
              className="thread-row__checkbox"
              checked={allVisibleSelected}
              disabled={disabled || bulkDeleting}
              onChange={toggleSelectAll}
              aria-label={allVisibleSelected ? deselectAllLabel(locale) : selectAllLabel(locale)}
            />
            <span>{allVisibleSelected ? deselectAllLabel(locale) : selectAllLabel(locale)}</span>
          </label>
        ) : null}
      </div>

      {hasSelection ? (
        <div className="nav-drawer__bulk-bar" dir={locale === 'he' ? 'rtl' : 'ltr'}>
          <div className="nav-drawer__bulk-actions">
            <button
              type="button"
              className="nav-drawer__bulk-delete"
              onClick={handleDeleteSelected}
              disabled={bulkDeleting || disabled}
            >
              <span className="nav-drawer__bulk-delete-icon" aria-hidden="true">
                🗑️
              </span>
              <span>{bulkDeleting ? (locale === 'he' ? 'מוחק…' : 'Deleting…') : deleteSelectedLabel(locale)}</span>
            </button>
            <button
              type="button"
              className="nav-drawer__bulk-clear"
              onClick={clearSelection}
              disabled={bulkDeleting || disabled}
            >
              {deselectAllLabel(locale)}
            </button>
          </div>
        </div>
      ) : null}

      <ul className="thread-list">
        {visibleThreads.map((t) => {
          const checked = selectedThreadIds.includes(t.id)
          return (
            <li
              key={t.id}
              className={`thread-row${checked ? ' thread-row--selected' : ''}${t.id === activeId ? ' thread-row--active' : ''}`}
            >
              <label className="thread-row__check" title={locale === 'he' ? 'בחר שיחה' : 'Select conversation'}>
                <input
                  type="checkbox"
                  className="thread-row__checkbox"
                  checked={checked}
                  disabled={disabled || bulkDeleting}
                  onChange={(e) => toggleThreadSelection(t.id, e.target.checked)}
                  onClick={(e) => e.stopPropagation()}
                  aria-label={t.title}
                />
              </label>
              <button
                type="button"
                className={t.id === activeId ? 'thread active' : 'thread'}
                onClick={() => onSelectThread?.(t.id)}
                disabled={bulkDeleting}
              >
                {t.title}
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export const NavDrawer = forwardRef(function NavDrawer({ open, onClose, overlay = true, children, footer = null }, ref) {
  return (
    <>
      {overlay ? (
        <button
          type="button"
          className={`nav-drawer-scrim${open ? ' nav-drawer-scrim--open' : ''}`}
          aria-label="Close menu"
          tabIndex={open ? 0 : -1}
          onClick={onClose}
        />
      ) : null}
      <aside
        ref={ref}
        className={`nav-drawer${overlay ? ' nav-drawer--overlay' : ' nav-drawer--docked'}${open ? ' nav-drawer--open' : ''}`}
        aria-hidden={!open}
      >
        <div className="nav-drawer__header">
          <span className="nav-drawer__title">Mission Control</span>
          <button type="button" className="nav-drawer__close" onClick={onClose} aria-label="Close menu">
            ×
          </button>
        </div>
        <div className="nav-drawer__body">{children}</div>
        {footer ? <div className="nav-drawer__footer">{footer}</div> : null}
      </aside>
    </>
  )
})
