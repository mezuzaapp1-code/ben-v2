/** Parse BEN API errors into user-safe messages (no raw JSON). */

export const CLERK_ORG_REQUIRED = 'clerk_org_required'

export const COUNCIL_BUSY = 'council_busy'
export const RUNTIME_SATURATED = 'runtime_saturated'
export const RETRY_LATER = 'retry_later'
export const DUPLICATE_REQUEST = 'duplicate_request'

const OVERLOAD_CODES = new Set([COUNCIL_BUSY, RUNTIME_SATURATED, RETRY_LATER, DUPLICATE_REQUEST])

const CLERK_ORG_MESSAGE =
  'Please select or create an organization in Clerk to continue.'

const CLERK_ORG_HINT =
  'Sign out and continue anonymously, or select an organization using the switcher above.'

export function parseBenErrorResponse(status, data) {
  const detail = data?.detail
  if (typeof detail === 'object' && detail !== null && OVERLOAD_CODES.has(detail.code)) {
    const hint = detail.hint ? String(detail.hint) : null
    const message = String(detail.message || 'Please try again shortly.')
    return {
      code: detail.code,
      message: hint ? `${message} ${hint}` : message,
      hint,
      recoverable: detail.recoverable !== false,
      retry_after_s: detail.retry_after_s,
    }
  }
  if (typeof detail === 'object' && detail !== null && detail.code === CLERK_ORG_REQUIRED) {
    return {
      code: CLERK_ORG_REQUIRED,
      message: String(detail.message || CLERK_ORG_MESSAGE),
      hint: String(detail.hint || CLERK_ORG_HINT),
      recoverable: true,
    }
  }
  if (typeof detail === 'string' && /organization context missing/i.test(detail)) {
    return {
      code: CLERK_ORG_REQUIRED,
      message: CLERK_ORG_MESSAGE,
      hint: CLERK_ORG_HINT,
      recoverable: true,
    }
  }
  if (status === 401) {
    return {
      code: 'auth_required',
      message: typeof detail === 'string' ? detail : 'Sign in required.',
      hint: null,
      recoverable: true,
    }
  }
  if (status === 422) {
    return {
      code: 'validation_error',
      message:
        typeof detail === 'string'
          ? detail
          : 'Invalid request. Check your session and try again.',
      hint: null,
      recoverable: true,
    }
  }
  if (status >= 500) {
    return {
      code: 'server_error',
      message: 'Service is temporarily unavailable. Please try again.',
      hint: null,
      recoverable: true,
    }
  }
  return null
}

export function humanizeBenHttpError(status, data) {
  const parsed = parseBenErrorResponse(status, data)
  if (parsed) return parsed.message
  if (typeof data?.detail === 'string') return data.detail
  return `Request failed (${status}). You can retry.`
}

export async function readJsonResponse(res) {
  try {
    return await res.json()
  } catch {
    return {}
  }
}

export async function throwIfNotOk(res) {
  const data = await readJsonResponse(res)
  if (!res.ok) {
    const err = new Error(humanizeBenHttpError(res.status, data))
    err.status = res.status
    err.data = data
    err.parsed = parseBenErrorResponse(res.status, data)
    throw err
  }
  return data
}
