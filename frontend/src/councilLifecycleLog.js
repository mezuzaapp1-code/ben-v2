/** Dev-only council lifecycle logging (no secrets, no tokens). */

const DEV = import.meta.env.DEV

export function logCouncilLifecycle(event, detail = {}) {
  if (!DEV) return
  const safe = { event, ...detail }
  if ('hasAuth' in safe) safe.hasAuth = Boolean(safe.hasAuth)
  if ('hasThreadId' in safe) safe.hasThreadId = Boolean(safe.hasThreadId)
  // eslint-disable-next-line no-console
  console.info('[ben.council]', safe)
}
