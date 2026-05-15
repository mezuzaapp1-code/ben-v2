import { createContext, useContext } from 'react'
import { useBenAuth as useClerkBenAuth } from '../hooks/useBenAuth.js'

const defaultAuth = {
  clerkEnabled: false,
  isLoaded: true,
  isSignedIn: false,
  getToken: async () => null,
}

const BenAuthContext = createContext(defaultAuth)

function ClerkBenAuthBridge({ children }) {
  const auth = useClerkBenAuth()
  return <BenAuthContext.Provider value={auth}>{children}</BenAuthContext.Provider>
}

export function BenAuthProvider({ children }) {
  const hasClerk = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())
  if (!hasClerk) {
    return <BenAuthContext.Provider value={defaultAuth}>{children}</BenAuthContext.Provider>
  }
  return <ClerkBenAuthBridge>{children}</ClerkBenAuthBridge>
}

export function useBenAuthContext() {
  return useContext(BenAuthContext)
}
