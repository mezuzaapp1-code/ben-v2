import { useAuth, useOrganization } from '@clerk/clerk-react'
import { createContext, useContext, useMemo } from 'react'
import {
  clerkSignedOutCreatePrivilege,
  shouldApplyBetaCreateOverride,
} from '../auth/clerkPersistentAccess.js'
import { isBetaAuthorized, isBetaGateEnabled } from '../lib/betaAuth.js'

const HAS_CLERK = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())
const ADMIN_ROLES = new Set(['org:admin', 'admin', 'owner', 'org:owner'])

const DEFAULT_PRIVILEGE = Object.freeze({ canCreate: false, reason: null })
const SIGNED_OUT_PRIVILEGE = clerkSignedOutCreatePrivilege()
const ProjectCreatePrivilegeContext = createContext(DEFAULT_PRIVILEGE)

function applyBetaOverride(privilege) {
  if (!shouldApplyBetaCreateOverride({ clerkEnabled: HAS_CLERK })) {
    return privilege
  }
  if (isBetaGateEnabled() && isBetaAuthorized()) {
    return { canCreate: true, reason: null }
  }
  return privilege
}

function organizationCreatePrivilege({ orgId, orgRole, membershipRole, orgLoaded }) {
  if (!orgId) {
    return { canCreate: true, reason: null }
  }
  const role = (membershipRole || orgRole || '').toLowerCase()
  if (ADMIN_ROLES.has(role) || role.endsWith(':admin')) {
    return { canCreate: true, reason: null }
  }
  if (!orgLoaded) {
    return { canCreate: false, reason: 'Checking permissions…' }
  }
  return { canCreate: false, reason: 'Admin or owner role required' }
}

function SignedInOrganizationPrivilegeProvider({ children }) {
  const { orgId, orgRole } = useAuth()
  const { membership, isLoaded: orgLoaded } = useOrganization()
  const value = useMemo(
    () =>
      organizationCreatePrivilege({
        orgId,
        orgRole,
        membershipRole: membership?.role,
        orgLoaded,
      }),
    [orgId, orgRole, membership?.role, orgLoaded]
  )
  return (
    <ProjectCreatePrivilegeContext.Provider value={value}>
      {children}
    </ProjectCreatePrivilegeContext.Provider>
  )
}

function ClerkProjectCreatePrivilegeProvider({ children }) {
  const { isSignedIn } = useAuth()
  if (!isSignedIn) {
    return (
      <ProjectCreatePrivilegeContext.Provider value={SIGNED_OUT_PRIVILEGE}>
        {children}
      </ProjectCreatePrivilegeContext.Provider>
    )
  }
  return <SignedInOrganizationPrivilegeProvider>{children}</SignedInOrganizationPrivilegeProvider>
}

function BetaProjectCreatePrivilegeProvider({ children }) {
  const value = useMemo(() => applyBetaOverride(DEFAULT_PRIVILEGE), [])
  return (
    <ProjectCreatePrivilegeContext.Provider value={value}>
      {children}
    </ProjectCreatePrivilegeContext.Provider>
  )
}

/**
 * When Clerk is configured, only a signed-in Clerk session may create projects.
 * AppGate / beta localStorage is not a production customer identity.
 * Without Clerk, local beta override remains available.
 */
export function ProjectCreatePrivilegeProvider({ children }) {
  if (HAS_CLERK) {
    return <ClerkProjectCreatePrivilegeProvider>{children}</ClerkProjectCreatePrivilegeProvider>
  }
  return <BetaProjectCreatePrivilegeProvider>{children}</BetaProjectCreatePrivilegeProvider>
}

export function useProjectCreatePrivilege() {
  return useContext(ProjectCreatePrivilegeContext)
}
