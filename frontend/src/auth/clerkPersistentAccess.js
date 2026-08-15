/**
 * Gate A.1 — Clerk session is the only production customer identity.
 * AppGate / beta localStorage must not unlock persistent product APIs.
 */

export const CLERK_SIGN_IN_REQUIRED = 'Sign in required'

export function isClerkPersistentSessionReady({ clerkEnabled, isLoaded, isSignedIn } = {}) {
  if (!clerkEnabled) return true
  return Boolean(isLoaded && isSignedIn)
}

export function shouldShowClerkSignIn({ clerkEnabled, isLoaded, isSignedIn } = {}) {
  return Boolean(clerkEnabled && isLoaded && !isSignedIn)
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
