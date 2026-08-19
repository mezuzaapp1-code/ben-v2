/**
 * Frontend tenant key for active-project scope.
 *
 * Server tenant is derived from the Clerk JWT only:
 *   - org_id / o.id present → organization tenant (tenant_id = org_id)
 *   - signed in, no org, personal allowed → personal tenant
 *
 * This helper uses the same Clerk session fields that populate that JWT
 * (useAuth().orgId / useAuth().userId). It does not invent a second
 * membership model. The key only needs to change when the security
 * context changes so active project can be cleared/rebound.
 */
export function resolveActiveTenantId({
  clerkEnabled = false,
  isSignedIn = false,
  orgId = null,
  userId = null,
} = {}) {
  if (!clerkEnabled) return 'local'
  if (!isSignedIn) return null
  const org = String(orgId || '').trim()
  if (org) return `org:${org}`
  const user = String(userId || '').trim()
  if (user) return `personal:${user}`
  return null
}

export function isOrganizationTenantId(tenantId) {
  return String(tenantId || '').startsWith('org:')
}

export function isPersonalTenantId(tenantId) {
  return String(tenantId || '').startsWith('personal:')
}
