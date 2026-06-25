import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import {
  getBetaAlias,
  getBetaOrgId,
  getBetaSessionHeaders,
  isBetaAuthorized,
  isBetaGateEnabled,
} from '../lib/betaAuth.js'

const BetaSessionContext = createContext({
  ready: true,
  authorized: false,
  alias: '',
  orgId: '',
  getSessionHeaders: () => ({}),
})

function readSessionSnapshot() {
  return {
    ready: true,
    authorized: isBetaAuthorized(),
    alias: getBetaAlias(),
    orgId: getBetaOrgId(),
  }
}

/** Keeps beta auditor session visible to React after AppGate authorization. */
export function BetaSessionProvider({ children }) {
  const [session, setSession] = useState(readSessionSnapshot)

  const refresh = useCallback(() => {
    setSession(readSessionSnapshot())
  }, [])

  useEffect(() => {
    const onSessionChange = () => refresh()
    window.addEventListener('basalt-beta-session', onSessionChange)
    window.addEventListener('storage', onSessionChange)
    return () => {
      window.removeEventListener('basalt-beta-session', onSessionChange)
      window.removeEventListener('storage', onSessionChange)
    }
  }, [refresh])

  const value = useMemo(
    () => ({
      ...session,
      getSessionHeaders: (betaContext = {}) => {
        if (!isBetaGateEnabled()) return {}
        if (!session.authorized && !session.alias) return {}
        return getBetaSessionHeaders(betaContext)
      },
    }),
    [session]
  )

  return <BetaSessionContext.Provider value={value}>{children}</BetaSessionContext.Provider>
}

export function useBetaSession() {
  return useContext(BetaSessionContext)
}
