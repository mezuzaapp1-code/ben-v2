import { useCallback, useMemo, useRef, useState } from 'react'
import { useAutoResizeTextarea } from '../hooks/useAutoResizeTextarea.js'
import { useDismissOnOutside } from '../hooks/useDismissOnOutside.js'
import { getSpeakingProviders } from '../providers/providerRegistry.js'
import { isProviderGloballyActive } from '../lib/globalFeatureCatalog.js'
import { EngineSettingsPanel } from './AdvancedEngineSettings.jsx'
import './ComposerCapsule.css'

/**
 * ChatGPT-style integrated composer: capsule shell, + attach menu, engine settings popover.
 */
export function ComposerCapsule({
  value,
  onChange,
  onSubmit,
  placeholder,
  disabled = false,
  canSend = false,
  sendLabel = 'Send',
  loading = false,
  adhocMode = false,
  shellAccent,
  attachMenuItems = [],
  attachMenuHidden = null,
  engineSettings = null,
  ariaLabel = 'Message',
}) {
  const { ref: textareaRef, syncHeight } = useAutoResizeTextarea(value, {
    minRows: 1,
    maxRows: 6,
  })

  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const attachWrapRef = useRef(null)
  const attachTriggerRef = useRef(null)
  const settingsWrapRef = useRef(null)
  const settingsTriggerRef = useRef(null)

  const closeAttachMenu = useCallback(() => setAttachMenuOpen(false), [])
  const closeSettings = useCallback(() => setSettingsOpen(false), [])

  useDismissOnOutside({
    open: attachMenuOpen,
    onDismiss: closeAttachMenu,
    containerRef: attachWrapRef,
    triggerRef: attachTriggerRef,
  })

  useDismissOnOutside({
    open: settingsOpen,
    onDismiss: closeSettings,
    containerRef: settingsWrapRef,
    triggerRef: settingsTriggerRef,
  })

  const submit = (event) => {
    event?.preventDefault?.()
    event?.stopPropagation?.()
    if (!canSend || disabled) return
    closeAttachMenu()
    closeSettings()
    onSubmit?.()
  }

  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit(event)
    }
  }

  const toggleAttachMenu = (event) => {
    event.preventDefault()
    event.stopPropagation()
    if (disabled) return
    closeSettings()
    setAttachMenuOpen((open) => !open)
  }

  const toggleSettings = (event) => {
    event.preventDefault()
    event.stopPropagation()
    if (disabled || !engineSettings) return
    closeAttachMenu()
    setSettingsOpen((open) => !open)
  }

  const runAttachItem = (item) => {
    closeAttachMenu()
    item.onClick?.()
  }

  const showEngineSettings = Boolean(engineSettings)

  const activeEngineChips = useMemo(() => {
    if (!engineSettings?.gateProviders) return []
    const catalogKeys = engineSettings.activeCatalogKeys ?? []
    return getSpeakingProviders().filter((provider) =>
      isProviderGloballyActive(catalogKeys, provider.id)
    )
  }, [engineSettings])

  const selectEngine = useCallback(
    (providerId) => {
      if (disabled) return
      engineSettings?.onProviderChange?.(providerId)
    },
    [disabled, engineSettings]
  )

  return (
    <form
      className={`composer-capsule${adhocMode ? ' composer-capsule--adhoc' : ''}`}
      style={shellAccent ? { '--capsule-accent': shellAccent } : undefined}
      onSubmit={submit}
      aria-label={ariaLabel}
    >
      {activeEngineChips.length > 0 ? (
        <div className="composer-capsule__chips" role="toolbar" aria-label="Active engines">
          {activeEngineChips.map((provider) => {
            const isActive = provider.id === engineSettings?.activeProviderId
            return (
              <button
                key={provider.id}
                type="button"
                className={[
                  'composer-capsule__chip',
                  isActive ? 'composer-capsule__chip--active' : '',
                ].join(' ')}
                style={{ '--chip-accent': provider.accent }}
                aria-pressed={isActive}
                disabled={disabled}
                onClick={() => selectEngine(provider.id)}
              >
                <span className="composer-capsule__chip-dot" aria-hidden="true" />
                <span className="composer-capsule__chip-label">{provider.shortLabel ?? provider.label}</span>
              </button>
            )
          })}
        </div>
      ) : null}
      <div
        className={`composer-capsule__shell${attachMenuOpen || settingsOpen ? ' composer-capsule__shell--menus-open' : ''}`}
      >
        <div className="composer-capsule__leading">
          <div ref={attachWrapRef} className="composer-capsule__plus-wrap">
            <button
              ref={attachTriggerRef}
              type="button"
              className="composer-capsule__plus"
              aria-label="Add attachments and actions"
              aria-expanded={attachMenuOpen}
              aria-haspopup="menu"
              disabled={disabled}
              onClick={toggleAttachMenu}
            >
              <svg className="composer-capsule__plus-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
            {attachMenuOpen && attachMenuItems.length > 0 ? (
              <div className="composer-capsule__attach-menu" role="menu">
                {attachMenuItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="menuitem"
                    className="composer-capsule__attach-menu-item"
                    disabled={disabled || item.disabled}
                    onClick={() => runAttachItem(item)}
                  >
                    {item.icon ? <span className="composer-capsule__attach-menu-icon">{item.icon}</span> : null}
                    <span>{item.label}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {attachMenuHidden ? (
              <div className="composer-capsule__attach-hidden" aria-hidden="true">
                {attachMenuHidden}
              </div>
            ) : null}
          </div>
          {showEngineSettings ? (
            <div
              ref={settingsWrapRef}
              className={`composer-capsule__settings-wrap${settingsOpen ? ' composer-capsule__settings-wrap--open' : ''}`}
            >
              <button
                ref={settingsTriggerRef}
                type="button"
                className="composer-capsule__settings"
                aria-label="Engine configuration"
                aria-expanded={settingsOpen}
                aria-haspopup="dialog"
                disabled={disabled}
                onClick={toggleSettings}
              >
                <svg
                  className="composer-capsule__settings-icon"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                  focusable="false"
                >
                  <path
                    d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    fill="none"
                  />
                  <path
                    d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1.08-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1.08 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.85 1 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
              {settingsOpen ? (
                <div className="composer-capsule__engine-popover" role="dialog" aria-label="Engine configuration">
                  <span className="composer-capsule__engine-popover-title">Engine</span>
                  <EngineSettingsPanel compact {...engineSettings} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        <textarea
          ref={textareaRef}
          className="composer-capsule__input"
          value={value}
          onChange={(event) => onChange?.(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          aria-label={ariaLabel}
          disabled={disabled}
          rows={1}
          enterKeyHint="send"
          onInput={syncHeight}
        />
        <div className="composer-capsule__actions">
          <button
            type="submit"
            className="composer-capsule__send"
            disabled={!canSend || disabled}
            aria-label={sendLabel}
            title={sendLabel}
          >
            {loading ? (
              <span className="composer-capsule__send-spinner" aria-hidden="true" />
            ) : (
              <svg
                className="composer-capsule__send-icon"
                viewBox="0 0 24 24"
                aria-hidden="true"
                focusable="false"
              >
                <path d="M3.4 20.6 21 12 3.4 3.4l1.8 7.2L16 12l-10.8 1.4 1.8 7.2Z" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </form>
  )
}
