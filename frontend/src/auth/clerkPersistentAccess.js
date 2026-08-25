/**
 * Gate A.1 — Clerk session is the only production customer identity.
 * AppGate / beta localStorage must not unlock persistent product APIs.
 *
 * Account chrome UI state is separate from persistentReady. Loading/unavailable
 * chrome must never unlock customer APIs.
 */

export const CLERK_SIGN_IN_REQUIRED = 'Sign in required'

export const ACCOUNT_CHROME_LOAD_TIMEOUT_MS = 12_000

export const ACCOUNT_CHROME_STATES = Object.freeze({
  loading: 'loading',
  signed_out: 'signed_out',
  signed_in: 'signed_in',
  unavailable: 'unavailable',
})

export function isClerkPersistentSessionReady({ clerkEnabled, isLoaded, isSignedIn } = {}) {
  if (!clerkEnabled) return true
  return Boolean(isLoaded && isSignedIn)
}

export function shouldShowClerkSignIn({ clerkEnabled, isLoaded, isSignedIn } = {}) {
  return Boolean(clerkEnabled && isLoaded && !isSignedIn)
}

export function resolveAccountChromeState({
  clerkEnabled = false,
  isLoaded = false,
  isSignedIn = false,
  loadTimedOut = false,
  clerkError = false,
} = {}) {
  if (!clerkEnabled) return null
  if (isLoaded && isSignedIn) return ACCOUNT_CHROME_STATES.signed_in
  if (isLoaded && !isSignedIn) return ACCOUNT_CHROME_STATES.signed_out
  if (clerkError || loadTimedOut) return ACCOUNT_CHROME_STATES.unavailable
  return ACCOUNT_CHROME_STATES.loading
}

export function publicAccountLabel({ emailAddress = '', fullName = '', firstName = '' } = {}) {
  const email = String(emailAddress || '').trim()
  const name = String(fullName || firstName || '').trim()
  if (email) return email
  if (name) return name
  return 'Signed in'
}

export async function switchAccountAfterSignOut(clerk) {
  if (!clerk || typeof clerk.signOut !== 'function') {
    throw new Error('sign_out_unavailable')
  }
  await clerk.signOut()
  if (typeof clerk.openSignIn === 'function') {
    clerk.openSignIn()
  }
}

export function shouldFetchPersistentCustomerApis(auth) {
  return isClerkPersistentSessionReady(auth)
}

export function shouldApplyBetaCreateOverride({ clerkEnabled } = {}) {
  return !clerkEnabled
}

export function clerkSignedOutCreatePrivilege() {
  return Object.freeze({ canCreate: false, reason: CLERK_SIGN_IN_REQUIRED })
}
