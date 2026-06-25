import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider } from '@clerk/clerk-react'
import './index.css'
import './theme/theme.css'
import App from './App.jsx'
import { BenAuthProvider } from './auth/BenAuthContext.jsx'
import { BetaSessionProvider } from './auth/BetaSessionContext.jsx'
import { ProjectCreatePrivilegeProvider } from './hooks/useProjectCreatePrivilege.jsx'
import { ThemeProvider } from './theme/ThemeContext.jsx'
import { AppGate } from './components/AppGate.jsx'
import { isBetaAuthorized, isBetaGateEnabled } from './lib/betaAuth.js'
import { registerHardwareBridges } from './index.jsx'

registerHardwareBridges()

const clerkPk = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim()

function GatedApp() {
  const [authorized, setAuthorized] = useState(() => isBetaAuthorized())

  if (isBetaGateEnabled() && !authorized) {
    return <AppGate onAuthorized={() => setAuthorized(true)} />
  }

  return (
    <BetaSessionProvider>
      <BenAuthProvider>
        <ProjectCreatePrivilegeProvider>
          <App />
        </ProjectCreatePrivilegeProvider>
      </BenAuthProvider>
    </BetaSessionProvider>
  )
}

function Root() {
  return (
    <ThemeProvider>
      <GatedApp />
    </ThemeProvider>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {clerkPk ? (
      <ClerkProvider publishableKey={clerkPk}>
        <Root />
      </ClerkProvider>
    ) : (
      <Root />
    )}
  </StrictMode>,
)
