/** Closed-beta passcode gate + auditor sandbox session (Task 010). */

export const BETA_AUTH_STORAGE_KEY = 'basalt-app-authorized'
export const BETA_ALIAS_STORAGE_KEY = 'basalt-beta-alias'
export const BETA_ORG_ID_STORAGE_KEY = 'basalt-beta-org-id'

export function isBetaGateEnabled() {
  return Boolean(import.meta.env?.VITE_BETA_PASSCODE?.trim())
}

export function configuredBetaPasscode() {
  return import.meta.env?.VITE_BETA_PASSCODE?.trim() || ''
}

export function getBetaAlias() {
  try {
    return localStorage.getItem(BETA_ALIAS_STORAGE_KEY)?.trim() || ''
  } catch {
    return ''
  }
}

export function getBetaOrgId() {
  try {
    return localStorage.getItem(BETA_ORG_ID_STORAGE_KEY)?.trim() || ''
  } catch {
    return ''
  }
}

export function isBetaAuthorized() {
  if (!isBetaGateEnabled()) return true
  try {
    return (
      localStorage.getItem(BETA_AUTH_STORAGE_KEY) === 'true' &&
      Boolean(getBetaAlias()) &&
      Boolean(getBetaOrgId())
    )
  } catch {
    return false
  }
}

export function setBetaSession({ alias, orgId }) {
  try {
    localStorage.setItem(BETA_AUTH_STORAGE_KEY, 'true')
    localStorage.setItem(BETA_ALIAS_STORAGE_KEY, alias)
    localStorage.setItem(BETA_ORG_ID_STORAGE_KEY, orgId)
  } catch {
    /* private browsing */
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('basalt-beta-session'))
  }
}

export function clearBetaAuthorization() {
  try {
    localStorage.removeItem(BETA_AUTH_STORAGE_KEY)
    localStorage.removeItem(BETA_ALIAS_STORAGE_KEY)
    localStorage.removeItem(BETA_ORG_ID_STORAGE_KEY)
  } catch {
    /* ignore */
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('basalt-beta-session'))
  }
}

export function validateBetaPasscode(input) {
  const expected = configuredBetaPasscode()
  if (!expected) return false
  return String(input || '').trim() === expected
}

export function validateBetaAlias(input) {
  const alias = String(input || '').trim()
  return /^[a-zA-Z0-9][a-zA-Z0-9 _.-]{0,63}$/.test(alias)
}

export async function resolveBetaSession(alias, passcode) {
  const res = await fetch('/api/beta/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ alias: alias.trim(), passcode }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error(data?.detail || 'Beta session resolution failed')
    err.status = res.status
    throw err
  }
  return data
}

export function getBetaPasscodeHeader() {
  if (!isBetaGateEnabled() || !isBetaAuthorized()) return {}
  return { 'X-Basalt-Beta-Passcode': configuredBetaPasscode() }
}

export function getBetaSessionHeaders({ theme, projectName } = {}) {
  if (!isBetaGateEnabled() || !isBetaAuthorized()) return {}
  const alias = getBetaAlias()
  if (!alias) return getBetaPasscodeHeader()

  const resolvedTheme =
    theme || (typeof document !== 'undefined' ? document.documentElement.getAttribute('data-theme') : null) || 'dark'

  const headers = {
    ...getBetaPasscodeHeader(),
    'X-Basalt-Beta-Alias': alias,
    'X-Basalt-Beta-Theme': resolvedTheme === 'light' ? 'light' : 'dark',
  }

  if (projectName?.trim()) {
    headers['X-Basalt-Beta-Project-Name'] = projectName.trim().slice(0, 512)
  }

  return headers
}
