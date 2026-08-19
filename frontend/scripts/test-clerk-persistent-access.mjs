/**
 * Gate A.1 — Clerk persistent-access helpers (no vitest).
 * Run: node frontend/scripts/test-clerk-persistent-access.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  CLERK_SIGN_IN_REQUIRED,
  clerkSignedOutCreatePrivilege,
  isClerkPersistentSessionReady,
  shouldApplyBetaCreateOverride,
  shouldFetchPersistentCustomerApis,
  shouldShowClerkSignIn,
} from '../src/auth/clerkPersistentAccess.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

assert(
  isClerkPersistentSessionReady({ clerkEnabled: false, isLoaded: false, isSignedIn: false }),
  'no Clerk → local path remains ready'
)
assert(
  !isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: false, isSignedIn: false }),
  'Clerk loading → not ready'
)
assert(
  !isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: false }),
  'Clerk signed out → not ready'
)
assert(
  isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: true }),
  'Clerk signed in → ready'
)

assert(
  !shouldFetchPersistentCustomerApis({ clerkEnabled: true, isLoaded: true, isSignedIn: false }),
  'signed out must not fetch persistent APIs'
)
assert(
  shouldFetchPersistentCustomerApis({ clerkEnabled: true, isLoaded: true, isSignedIn: true }),
  'signed in may fetch persistent APIs'
)

assert(
  !shouldShowClerkSignIn({ clerkEnabled: true, isLoaded: false, isSignedIn: false }),
  'do not flash Sign in before Clerk loads'
)
assert(
  shouldShowClerkSignIn({ clerkEnabled: true, isLoaded: true, isSignedIn: false }),
  'Sign in visible when Clerk loaded and signed out'
)
assert(
  !shouldShowClerkSignIn({ clerkEnabled: true, isLoaded: true, isSignedIn: true }),
  'Sign in hidden after session exists'
)

assert(!shouldApplyBetaCreateOverride({ clerkEnabled: true }), 'AppGate must not grant create when Clerk is configured')
assert(shouldApplyBetaCreateOverride({ clerkEnabled: false }), 'beta override only without Clerk')

const signedOut = clerkSignedOutCreatePrivilege()
assert(signedOut.canCreate === false, 'signed-out cannot create')
assert(signedOut.reason === CLERK_SIGN_IN_REQUIRED, 'signed-out reason is Sign in required')

const privilegeSrc = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../src/hooks/useProjectCreatePrivilege.jsx'),
  'utf8'
)
const signedInBlock = privilegeSrc.split('function SignedInOrganizationPrivilegeProvider')[1] || ''
assert(
  privilegeSrc.includes('SIGNED_OUT_PRIVILEGE') &&
    privilegeSrc.includes('if (!isSignedIn)') &&
    signedInBlock.includes('useOrganization()') &&
    !privilegeSrc.split('function SignedInOrganizationPrivilegeProvider')[0].includes('useOrganization()'),
  'useOrganization must only live in the signed-in provider'
)
assert(
  !privilegeSrc.includes('applyBetaOverride(clerkPrivilege)'),
  'Clerk path must not apply AppGate create override'
)

const appSrc = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../src/App.jsx'), 'utf8')
assert(appSrc.includes('shouldShowClerkSignIn'), 'App shows Clerk sign-in helper')
assert(appSrc.includes('persistentHeaders'), 'App uses stable persistent header builder')
assert(appSrc.includes('buildAppHeadersRef'), 'App keeps latest getToken behind a ref')
assert(appSrc.includes('ClerkSignInBanner'), 'App has main-shell Sign in banner')
assert(appSrc.includes('if (!persistentReady)'), 'App gates persistent fetches')
assert(appSrc.includes('fetchProjects'), 'projects client still used after sign-in')
assert(appSrc.includes('fetchThreadList'), 'threads client still used after sign-in')
assert(appSrc.includes('sessionTenantId'), 'App tracks Clerk tenant separately from persistentReady')
assert(appSrc.includes('resolveActiveTenantId'), 'App resolves org vs personal tenant from Clerk orgId/userId')

const benAuthSrc = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../src/hooks/useBenAuth.js'),
  'utf8'
)
assert(benAuthSrc.includes('orgId'), 'useBenAuth exposes Clerk orgId')
assert(benAuthSrc.includes('userId'), 'useBenAuth exposes Clerk userId')
assert(
  isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: true }),
  'org switch does not change persistentReady (function ignores orgId)'
)

console.log('PASS clerk persistent access + source guards')
