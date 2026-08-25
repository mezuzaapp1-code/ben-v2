import { SignInButton, SignOutButton, UserButton, useAuth, useClerk, useUser } from '@clerk/clerk-react'
import { Component, useEffect, useState } from 'react'
import {
  ACCOUNT_CHROME_LOAD_TIMEOUT_MS,
  ACCOUNT_CHROME_STATES,
  publicAccountLabel,
  resolveAccountChromeState,
  switchAccountAfterSignOut,
} from '../auth/clerkPersistentAccess.js'
import './AccountChrome.css'

class AccountChromeErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return <UnavailableChrome onReload={this.props.onReload} />
    }
    return this.props.children
  }
}

function UnavailableChrome({ onReload }) {
  return (
    <div className="account-chrome account-chrome--unavailable" data-account-chrome="unavailable">
      <p className="account-chrome__status account-chrome__unavailable" role="alert">
        Sign in unavailable
      </p>
      <button type="button" className="auth-btn account-chrome__reload" onClick={onReload}>
        Reload
      </button>
    </div>
  )
}

function reloadPage() {
  window.location.reload()
}

function SignedInChrome() {
  const clerk = useClerk()
  const { user } = useUser()
  const [switching, setSwitching] = useState(false)
  const label = publicAccountLabel({
    emailAddress: user?.primaryEmailAddress?.emailAddress || '',
    fullName: user?.fullName || '',
    firstName: user?.firstName || '',
  })

  const handleSwitchAccount = async () => {
    if (switching) return
    setSwitching(true)
    try {
      await switchAccountAfterSignOut(clerk)
    } catch {
      setSwitching(false)
    }
  }

  return (
    <div className="account-chrome account-chrome--signed-in" data-account-chrome="signed_in">
      <UserButton appearance={{ elements: { userButtonAvatarBox: { width: '1.65rem', height: '1.65rem' } } }} />
      <span className="account-chrome__identity" title={label}>
        {label}
      </span>
      <div className="account-chrome__actions">
        <SignOutButton>
          <button type="button" className="auth-btn">
            Sign out
          </button>
        </SignOutButton>
        <button
          type="button"
          className="auth-btn"
          onClick={handleSwitchAccount}
          disabled={switching}
        >
          {switching ? 'Switching…' : 'Switch account'}
        </button>
      </div>
    </div>
  )
}

function AccountChromeInner() {
  const { isLoaded, isSignedIn } = useAuth()
  const [loadTimedOut, setLoadTimedOut] = useState(false)

  useEffect(() => {
    if (isLoaded) return undefined
    const timer = window.setTimeout(() => {
      setLoadTimedOut(true)
    }, ACCOUNT_CHROME_LOAD_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [isLoaded])

  const state = resolveAccountChromeState({
    clerkEnabled: true,
    isLoaded,
    isSignedIn,
    loadTimedOut: isLoaded ? false : loadTimedOut,
  })

  if (state === ACCOUNT_CHROME_STATES.signed_in) {
    return <SignedInChrome />
  }

  if (state === ACCOUNT_CHROME_STATES.signed_out) {
    return (
      <div className="account-chrome account-chrome--signed-out" data-account-chrome="signed_out">
        <SignInButton mode="modal">
          <button type="button" className="auth-btn auth-btn--signin">
            Sign in
          </button>
        </SignInButton>
      </div>
    )
  }

  if (state === ACCOUNT_CHROME_STATES.unavailable) {
    return <UnavailableChrome onReload={reloadPage} />
  }

  return (
    <div className="account-chrome account-chrome--loading" data-account-chrome="loading">
      <p className="account-chrome__status" aria-busy="true" aria-live="polite">
        Checking account…
      </p>
    </div>
  )
}

export function AccountChrome() {
  return (
    <AccountChromeErrorBoundary onReload={reloadPage}>
      <AccountChromeInner />
    </AccountChromeErrorBoundary>
  )
}
