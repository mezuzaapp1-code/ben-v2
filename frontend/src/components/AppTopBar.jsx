import { ThemeToggle } from './ThemeToggle.jsx'
import { useDismissOnOutside } from '../hooks/useDismissOnOutside.js'
import './AppTopBar.css'

function HamburgerIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M4 7h16M4 12h16M4 17h16"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  )
}

function SettingsIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.75" fill="none" />
      <path
        d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  )
}

function SettingsPanel({ open, onClose, panelRef, triggerRef, authControls = null }) {
  useDismissOnOutside({ open, onDismiss: onClose, containerRef: panelRef, triggerRef })

  return (
    <div
      ref={panelRef}
      className={`app-settings${open ? ' app-settings--open' : ''}`}
      role="dialog"
      aria-label="Settings"
      hidden={!open}
    >
      <div className="app-settings__section">
        <span className="app-settings__label">Appearance</span>
        <ThemeToggle />
      </div>
      {authControls ? <div className="app-settings__section app-settings__section--auth">{authControls}</div> : null}
    </div>
  )
}

export function AppTopBar({
  menuButtonRef,
  onMenuClick,
  menuOpen = false,
  settingsButtonRef,
  settingsOpen,
  onSettingsClick,
  onSettingsClose,
  settingsPanelRef,
  authControls = null,
  shellAuth = null,
}) {
  return (
    <header className={`app-topbar${shellAuth ? ' app-topbar--with-shell-auth' : ''}`}>
      <button
        ref={menuButtonRef}
        type="button"
        className="app-topbar__icon-btn"
        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
        aria-expanded={menuOpen}
        onClick={onMenuClick}
      >
        <HamburgerIcon />
      </button>
      <span className="app-topbar__brand">BEN</span>
      {shellAuth ? <div className="app-topbar__shell-auth">{shellAuth}</div> : null}
      <div className="app-topbar__settings-wrap">
        <button
          ref={settingsButtonRef}
          type="button"
          className="app-topbar__icon-btn"
          aria-label="Settings"
          aria-expanded={settingsOpen}
          onClick={onSettingsClick}
        >
          <SettingsIcon />
        </button>
        <SettingsPanel
          open={settingsOpen}
          onClose={onSettingsClose}
          panelRef={settingsPanelRef}
          triggerRef={settingsButtonRef}
          authControls={authControls}
        />
      </div>
    </header>
  )
}
