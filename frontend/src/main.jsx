import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider } from '@clerk/clerk-react'
import './index.css'
import App from './App.jsx'
import { BenAuthProvider } from './auth/BenAuthContext.jsx'

const clerkPk = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim()

function Root() {
  return (
    <BenAuthProvider>
      <App />
    </BenAuthProvider>
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
