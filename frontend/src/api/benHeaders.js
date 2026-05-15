/**
 * Build JSON request headers for BEN API calls.
 * Adds Clerk Bearer token when available; never logs or persists the token.
 */

export async function buildBenHeaders(getToken, extraHeaders = {}) {
  const headers = {
    'Content-Type': 'application/json',
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
