import { useAuth } from '@clerk/clerk-react'
import { useCallback, useMemo } from 'react'

const HAS_CLERK = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())

/**
 * Safe token accessor for BEN backend calls.
 * Returns null when Clerk is not configured or user is signed out.
 */
export function useBenAuth() {
  const { isLoaded, isSignedIn, getToken: clerkGetToken, orgId, userId } = useAuth()

  const getToken = useCallback(async () => {
    if (!HAS_CLERK || !isLoaded || !isSignedIn) {
      return null
    }
    try {
      return (await clerkGetToken()) ?? null
    } catch {
      return null
    }
  }, [isLoaded, isSignedIn, clerkGetToken, orgId])

  return useMemo(
    () => ({
      clerkEnabled: HAS_CLERK,
      isLoaded: HAS_CLERK ? isLoaded : true,
      isSignedIn: HAS_CLERK && isSignedIn,
      orgId: HAS_CLERK && isSignedIn ? orgId || null : null,
      userId: HAS_CLERK && isSignedIn ? userId || null : null,
      getToken,
    }),
    [isLoaded, isSignedIn, getToken, orgId, userId]
  )
}
