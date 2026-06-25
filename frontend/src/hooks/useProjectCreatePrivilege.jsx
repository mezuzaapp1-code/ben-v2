import { useAuth, useOrganization } from '@clerk/clerk-react'
import { createContext, useContext, useMemo } from 'react'
import { isBetaAuthorized, isBetaGateEnabled } from '../lib/betaAuth.js'

const HAS_CLERK = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())
const ADMIN_ROLES = new Set(['org:admin', 'admin', 'owner', 'org:owner'])

const DEFAULT_PRIVILEGE = Object.freeze({ canCreate: false, reason: null })
const ProjectCreatePrivilegeContext = createContext(DEFAULT_PRIVILEGE)

function applyBetaOverride(privilege) {
  if (isBetaGateEnabled() && isBetaAuthorized()) {
    return { canCreate: true, reason: null }
  }
  return privilege
}

function useClerkProjectCreatePrivilege() {
  const { isSignedIn, orgId, orgRole } = useAuth()
  const { membership, isLoaded: orgLoaded } = useOrganization()

  return useMemo(() => {
    if (!isSignedIn) {
      return DEFAULT_PRIVILEGE
    }
    if (!orgId) {
      return { canCreate: true, reason: null }
    }
    const role = (membership?.role || orgRole || '').toLowerCase()
    if (ADMIN_ROLES.has(role) || role.endsWith(':admin')) {
      return { canCreate: true, reason: null }
    }
    if (!orgLoaded) {
      return { canCreate: false, reason: 'Checking permissions…' }
    }
    return { canCreate: false, reason: 'Admin or owner role required' }
  }, [isSignedIn, orgId, orgRole, membership?.role, orgLoaded])
}

function ClerkProjectCreatePrivilegeProvider({ children }) {
  const clerkPrivilege = useClerkProjectCreatePrivilege()
  const value = useMemo(() => applyBetaOverride(clerkPrivilege), [clerkPrivilege])
  return (
    <ProjectCreatePrivilegeContext.Provider value={value}>
      {children}
    </ProjectCreatePrivilegeContext.Provider>
  )
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
 * Beta-authorized users may create projects via BEN_ANONYMOUS_ORG_ID without Clerk.
 * Signed-in Clerk users retain org-admin rules when Clerk is configured.
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
