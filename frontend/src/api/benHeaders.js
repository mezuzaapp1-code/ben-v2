/**
 * Build JSON request headers for BEN API calls.
 * Adds Clerk Bearer token when available; attaches beta sandbox session headers.
 */

import { getBetaSessionHeaders } from '../lib/betaAuth.js'

export const AUTH_TOKEN_UNAVAILABLE = 'auth_token_unavailable'

export class AuthTokenUnavailableError extends Error {
  constructor(message = 'Sign in required.') {
    super(message)
    this.name = 'AuthTokenUnavailableError'
    this.code = AUTH_TOKEN_UNAVAILABLE
  }
}

export function isAuthTokenUnavailable(err) {
  return Boolean(
    err &&
      (err.code === AUTH_TOKEN_UNAVAILABLE || err.name === 'AuthTokenUnavailableError')
  )
}

export function hasPersistentAuthHeader(headers = {}) {
  const bearer = String(headers.Authorization || headers.authorization || '').trim()
  if (/^bearer\s+\S+/i.test(bearer)) return true
  const pass = String(
    headers['X-Basalt-Beta-Passcode'] || headers['x-basalt-beta-passcode'] || ''
  ).trim()
  const alias = String(
    headers['X-Basalt-Beta-Alias'] || headers['x-basalt-beta-alias'] || ''
  ).trim()
  return Boolean(pass && alias)
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Wait until a header builder produces a real persistent identity.
 * Does not send the caller request. Throws if acquisition definitively fails.
 */
export async function acquirePersistentHeaders(
  buildHeaders,
  { attempts = 6, delayMs = 200 } = {}
) {
  if (typeof buildHeaders !== 'function') {
    throw new AuthTokenUnavailableError()
  }
  const tries = Math.max(1, Number(attempts) || 1)
  const wait = Math.max(0, Number(delayMs) || 0)
  let lastErr = new AuthTokenUnavailableError()
  for (let i = 0; i < tries; i += 1) {
    try {
      const headers = await buildHeaders()
      if (hasPersistentAuthHeader(headers)) return headers
      lastErr = new AuthTokenUnavailableError()
    } catch (err) {
      if (!isAuthTokenUnavailable(err)) throw err
      lastErr = err
    }
    if (i + 1 < tries && wait) await sleep(wait)
  }
  throw lastErr
}

export async function buildBenHeaders(
  getToken,
  extraHeaders = {},
  betaContext = {},
  betaSessionHeaders = null,
  { requireAuthorization = false } = {}
) {
  const headers = {
    'Content-Type': 'application/json',
    ...(betaSessionHeaders ?? getBetaSessionHeaders(betaContext)),
    ...extraHeaders,
  }
  if (!getToken) {
    if (requireAuthorization && !hasPersistentAuthHeader(headers)) {
      throw new AuthTokenUnavailableError()
    }
    return headers
  }
  try {
    const token = await getToken()
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
  } catch {
    if (requireAuthorization && !hasPersistentAuthHeader(headers)) {
      throw new AuthTokenUnavailableError()
    }
    return headers
  }
  if (requireAuthorization && !hasPersistentAuthHeader(headers)) {
    throw new AuthTokenUnavailableError()
  }
  return headers
}
