/**
 * Persistent Clerk account chrome — UI state only.
 * Run: node frontend/scripts/test-account-chrome.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  ACCOUNT_CHROME_LOAD_TIMEOUT_MS,
  ACCOUNT_CHROME_STATES,
  isClerkPersistentSessionReady,
  publicAccountLabel,
  resolveAccountChromeState,
  shouldShowClerkSignIn,
  switchAccountAfterSignOut,
} from '../src/auth/clerkPersistentAccess.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

assert(ACCOUNT_CHROME_LOAD_TIMEOUT_MS >= 8_000, 'load timeout is conservative')

assert(
  resolveAccountChromeState({ clerkEnabled: false, isLoaded: false, isSignedIn: false }) === null,
  'no Clerk → no chrome'
)
assert(
  resolveAccountChromeState({ clerkEnabled: true, isLoaded: false, isSignedIn: false }) ===
    ACCOUNT_CHROME_STATES.loading,
  'Clerk loading → checking account'
)
assert(
  resolveAccountChromeState({
    clerkEnabled: true,
    isLoaded: false,
    isSignedIn: false,
    loadTimedOut: true,
  }) === ACCOUNT_CHROME_STATES.unavailable,
  'hung Clerk load → unavailable'
)
assert(
  resolveAccountChromeState({
    clerkEnabled: true,
    isLoaded: false,
    isSignedIn: false,
    clerkError: true,
  }) === ACCOUNT_CHROME_STATES.unavailable,
  'Clerk init error → unavailable'
)
assert(
  resolveAccountChromeState({ clerkEnabled: true, isLoaded: true, isSignedIn: false }) ===
    ACCOUNT_CHROME_STATES.signed_out,
  'loaded signed out → Sign in'
)
assert(
  resolveAccountChromeState({
    clerkEnabled: true,
    isLoaded: true,
    isSignedIn: false,
    loadTimedOut: true,
  }) === ACCOUNT_CHROME_STATES.signed_out,
  'loaded signed out wins over stale timeout'
)
assert(
  resolveAccountChromeState({ clerkEnabled: true, isLoaded: true, isSignedIn: true }) ===
    ACCOUNT_CHROME_STATES.signed_in,
  'signed in → identity chrome'
)
assert(
  resolveAccountChromeState({
    clerkEnabled: true,
    isLoaded: true,
    isSignedIn: true,
    clerkError: true,
  }) === ACCOUNT_CHROME_STATES.signed_in,
  'loaded signed-in is not unavailable'
)

assert(
  !isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: false, isSignedIn: false }),
  'loading chrome does not unlock customer APIs'
)
assert(
  !isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: true, isSignedIn: false }),
  'signed-out chrome does not unlock customer APIs'
)
assert(
  !isClerkPersistentSessionReady({ clerkEnabled: true, isLoaded: false, isSignedIn: true }),
  'unavailable/loading never becomes persistentReady from isSignedIn alone'
)
assert(
  !shouldShowClerkSignIn({ clerkEnabled: true, isLoaded: false, isSignedIn: false }),
  'banner must not flash Sign in while Clerk loads'
)

assert(publicAccountLabel({ emailAddress: 'ben@basalt.co.il' }) === 'ben@basalt.co.il', 'email identity')
assert(publicAccountLabel({ fullName: 'Smoke B', firstName: 'Smoke' }) === 'Smoke B', 'name fallback')
assert(publicAccountLabel({}) === 'Signed in', 'generic signed-in label without ids')
{
  const label = publicAccountLabel({
    emailAddress: 'a@b.co',
    userId: 'user_secret',
    orgId: 'org_secret',
    token: 'eyJhbGciOiJIUzI1NiJ9.aaa.bbb',
  })
  assert(label === 'a@b.co', 'identity prefers email')
  assert(!label.includes('user_'), 'identity omits Clerk user id')
  assert(!label.includes('org_'), 'identity omits org id')
  assert(!label.includes('eyJ'), 'identity omits JWT')
}

{
  const order = []
  let opened = false
  const pending = switchAccountAfterSignOut({
    async signOut() {
      order.push('signOut-start')
      await new Promise((resolve) => setTimeout(resolve, 5))
      order.push('signOut-done')
    },
    openSignIn() {
      opened = true
      order.push('openSignIn')
    },
  })
  assert(opened === false, 'must not open Sign in before signOut settles')
  await pending
  assert(opened === true, 'opens Sign in after signOut')
  assert(order.join(',') === 'signOut-start,signOut-done,openSignIn', 'sign out completes first')
}

{
  let opened = false
  let threw = false
  try {
    await switchAccountAfterSignOut({
      async signOut() {
        throw new Error('sign_out_failed')
      },
      openSignIn() {
        opened = true
      },
    })
  } catch {
    threw = true
  }
  assert(threw, 'failed sign-out propagates')
  assert(opened === false, 'must not open Sign in if sign-out failed')
}

const chromeSrc = readFileSync(join(root, 'src/components/AccountChrome.jsx'), 'utf8')
assert(chromeSrc.includes('Checking account…'), 'loading copy is Checking account…')
assert(chromeSrc.includes('Sign in unavailable'), 'unavailable copy is explicit')
assert(chromeSrc.includes('<SignInButton mode="modal">'), 'signed-out uses Clerk modal')
assert(chromeSrc.includes('<SignOutButton>'), 'signed-in has explicit Sign out')
assert(chromeSrc.includes('Switch account'), 'signed-in has explicit Switch account')
assert(chromeSrc.includes('switchAccountAfterSignOut(clerk)'), 'switch uses sign-out-then-sign-in helper')
assert(chromeSrc.includes('<UserButton'), 'reuses Clerk UserButton')
assert(!chromeSrc.includes('/sign-in'), 'no /sign-in route')
assert(!chromeSrc.includes('/login'), 'no /login route')
assert(!chromeSrc.includes('orgId'), 'chrome source does not render orgId')
assert(!chromeSrc.includes('userId'), 'chrome source does not render userId')
assert(!chromeSrc.includes('getToken'), 'chrome source does not touch tokens')
assert(!chromeSrc.includes('Authorization'), 'chrome source does not print Authorization')
assert(!/jwt/i.test(chromeSrc), 'chrome source does not mention JWT')
assert(chromeSrc.includes('window.location.reload'), 'unavailable is recoverable via reload')

const cssSrc = readFileSync(join(root, 'src/components/AccountChrome.css'), 'utf8')
assert(cssSrc.includes('text-overflow: ellipsis'), 'identity truncates')
assert(cssSrc.includes('max-width: 5.75rem'), 'mobile identity max-width is tight')

const appSrc = readFileSync(join(root, 'src/App.jsx'), 'utf8')
assert(appSrc.includes('shellAuth={HAS_CLERK_UI ? <AccountChrome /> : null}'), 'top bar always has chrome when Clerk is configured')
assert(appSrc.includes('authControls={HAS_CLERK_UI ? <ClerkAuthControls /> : null}'), 'Settings auth remains secondary')
assert(appSrc.includes('showClerkSignIn ? <ClerkSignInBanner /> : null'), 'signed-out banner remains')
assert(appSrc.includes("if (!isLoaded)"), 'Settings does not flash Sign in while Clerk loads')
assert(!appSrc.includes("path: '/sign-in'") && !appSrc.includes('"/sign-in"'), 'App does not add a /sign-in route')
assert(!appSrc.includes('"/login"'), 'App does not add a /login route')

const readySrc = readFileSync(join(root, 'src/auth/clerkPersistentAccess.js'), 'utf8')
const readyFn = readySrc.split('export function isClerkPersistentSessionReady')[1].split('export function')[0]
assert(readyFn.includes('if (!clerkEnabled) return true'), 'persistentReady still skips Clerk-disabled local path')
assert(
  readyFn.includes('return Boolean(isLoaded && isSignedIn)'),
  'isClerkPersistentSessionReady semantics unchanged'
)
assert(!readyFn.includes('loadTimedOut'), 'persistentReady ignores chrome timeout')

console.log('PASS account chrome state + source guards')
