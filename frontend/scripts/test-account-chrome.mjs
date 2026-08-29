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
const signedInFn = chromeSrc.split('function SignedInChrome')[1]?.split('function AccountChromeInner')[0] || ''
const innerFn = chromeSrc.split('function AccountChromeInner')[1] || ''
assert(signedInFn.includes('useUser()'), 'identity is read live from Clerk useUser')
assert(!/useState\([^)]*email|useState\([^)]*name/i.test(signedInFn), 'identity is not cached in local state')
assert(!innerFn.includes('useUser'), 'signed-out/loading chrome cannot see previous useUser')
assert(
  innerFn.includes('ACCOUNT_CHROME_STATES.signed_in') && innerFn.includes('<SignedInChrome />'),
  'identity chrome mounts only while signed_in'
)
assert(
  innerFn.includes('ACCOUNT_CHROME_STATES.signed_out') &&
    !innerFn
      .split('ACCOUNT_CHROME_STATES.signed_out')[1]
      .split('ACCOUNT_CHROME_STATES.unavailable')[0]
      .includes('account-chrome__identity'),
  'signed-out branch has no identity label'
)
assert(
  resolveAccountChromeState({ clerkEnabled: true, isLoaded: true, isSignedIn: false }) !==
    ACCOUNT_CHROME_STATES.signed_in,
  'session loss/sign-out cannot remain signed_in'
)

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
assert(cssSrc.includes('flex-wrap: wrap'), 'narrow signed-in chrome wraps instead of overflowing')
assert(cssSrc.includes('max-width: 7.25rem') || cssSrc.includes('max-width: 5.75rem'), 'mobile identity max-width is tight')
assert(cssSrc.includes('max-width: 100%'), 'chrome cannot exceed sidebar footer')
assert(cssSrc.includes('account-chrome__menu'), 'signed-in actions live in a disclosure menu')
assert(chromeSrc.includes('account-chrome__trigger'), 'account row discloses Sign out / Switch account')
assert(chromeSrc.includes('aria-haspopup="menu"'), 'account trigger is a menu button')

const drawerSrc = readFileSync(join(root, 'src/components/NavDrawer.jsx'), 'utf8')
const drawerCss = readFileSync(join(root, 'src/components/NavDrawer.css'), 'utf8')
assert(drawerSrc.includes('nav-drawer__footer'), 'drawer has a persistent footer slot')
assert(drawerCss.includes('.nav-drawer__footer'), 'footer is styled outside the scrolling body')
assert(drawerCss.includes('flex-shrink: 0'), 'footer does not scroll with history')
assert(drawerCss.includes('.nav-drawer__body'), 'sidebar content still scrolls in the body')

const appSrc = readFileSync(join(root, 'src/App.jsx'), 'utf8')
assert(
  appSrc.includes('footer={HAS_CLERK_UI ? <AccountChrome /> : null}'),
  'sidebar footer owns account chrome when Clerk is configured'
)
assert(!appSrc.includes('shellAuth='), 'account chrome is not mounted in the top bar')
assert(appSrc.includes('authControls={HAS_CLERK_UI ? <ClerkAuthControls /> : null}'), 'Settings auth remains secondary')
assert(appSrc.includes('<OrganizationSwitcher hidePersonal />'), 'Settings org switcher remains')
assert(appSrc.includes('showClerkSignIn ? <ClerkSignInBanner /> : null'), 'signed-out banner remains')
assert(appSrc.includes("if (!isLoaded)"), 'Settings does not flash Sign in while Clerk loads')
assert(!appSrc.includes("path: '/sign-in'") && !appSrc.includes('"/sign-in"'), 'App does not add a /sign-in route')
assert(!appSrc.includes('"/login"'), 'App does not add a /login route')
assert(appSrc.includes('usePlatformActiveFeatures(persistentReady ? persistentHeaders : null)'), 'platform features do not fetch while unresolved')
assert(appSrc.includes('if (!persistentReady || !tenantAtStart)'), 'projects fetch is gated on persistentReady')
assert(appSrc.includes('if (!persistentReady) {\n        setHydrating(false)'), 'thread hydrate does not fetch while unresolved')
assert(
  appSrc.includes('buildHeaders: persistentReady ? persistentHeaders : null'),
  'file inventory is unconfigured while unresolved'
)
assert(appSrc.includes('if (loading || !persistentReady) return false'), 'composer/chat cannot send while unresolved')

const topBarSrc = readFileSync(join(root, 'src/components/AppTopBar.jsx'), 'utf8')
const topBarCss = readFileSync(join(root, 'src/components/AppTopBar.css'), 'utf8')
assert(!topBarSrc.includes('shellAuth'), 'AppTopBar no longer accepts shell auth')
assert(!topBarCss.includes('app-topbar__shell-auth'), 'top bar has no auth slot')

const readySrc = readFileSync(join(root, 'src/auth/clerkPersistentAccess.js'), 'utf8')
const readyFn = readySrc.split('export function isClerkPersistentSessionReady')[1].split('export function')[0]
assert(readyFn.includes('if (!clerkEnabled) return true'), 'persistentReady still skips Clerk-disabled local path')
assert(
  readyFn.includes('return Boolean(isLoaded && isSignedIn)'),
  'isClerkPersistentSessionReady semantics unchanged'
)
assert(!readyFn.includes('loadTimedOut'), 'persistentReady ignores chrome timeout')

console.log('PASS account chrome state + source guards')
