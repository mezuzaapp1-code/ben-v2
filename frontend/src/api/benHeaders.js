/**
 * Build JSON request headers for BEN API calls.
 * Adds Clerk Bearer token when available; attaches beta sandbox session headers.
 */

import { getBetaSessionHeaders } from '../lib/betaAuth.js'

export async function buildBenHeaders(
  getToken,
  extraHeaders = {},
  betaContext = {},
  betaSessionHeaders = null
) {
  const headers = {
    'Content-Type': 'application/json',
    ...(betaSessionHeaders ?? getBetaSessionHeaders(betaContext)),
    ...extraHeaders,
  }
  if (!getToken) {
    return headers
  }
  try {
    const token = await getToken()
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
  } catch {
    // No session or token unavailable — proceed without Authorization (shadow mode).
  }
  return headers
}
