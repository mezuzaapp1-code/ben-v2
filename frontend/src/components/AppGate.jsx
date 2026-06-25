import { useState } from 'react'
import {
  resolveBetaSession,
  setBetaSession,
  validateBetaAlias,
  validateBetaPasscode,
} from '../lib/betaAuth.js'
import './AppGate.css'

export function AppGate({ onAuthorized }) {
  const [step, setStep] = useState('passcode')
  const [passcode, setPasscode] = useState('')
  const [alias, setAlias] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const handlePasscodeSubmit = (e) => {
    e.preventDefault()
    setError(null)
    if (!validateBetaPasscode(passcode)) {
      setError('Invalid passcode. Contact your Basalt beta coordinator.')
      return
    }
    setStep('alias')
  }

  const handleAliasSubmit = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    if (!validateBetaAlias(alias)) {
      setError('Use 1–64 characters: letters, numbers, spaces, dots, or hyphens.')
      setSubmitting(false)
      return
    }
    try {
      const session = await resolveBetaSession(alias, passcode)
      setBetaSession({ alias: session.alias, orgId: session.org_id })
      onAuthorized?.()
    } catch (err) {
      setError(err.message || 'Could not start auditor sandbox.')
      setSubmitting(false)
    }
  }

  return (
    <div className="app-gate" role="dialog" aria-modal="true" aria-label="Beta access gate">
      {step === 'passcode' ? (
        <form className="app-gate__panel" onSubmit={handlePasscodeSubmit}>
          <div className="app-gate__brand">BEN</div>
          <h1 className="app-gate__title">Basalt Closed Beta</h1>
          <label className="app-gate__label" htmlFor="beta-passcode">
            Enter Authorized Beta Passcode
          </label>
          <input
            id="beta-passcode"
            className="app-gate__input"
            type="password"
            autoComplete="off"
            autoFocus
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            placeholder="••••••••"
          />
          {error ? <p className="app-gate__error">{error}</p> : null}
          <button type="submit" className="app-gate__submit" disabled={!passcode.trim()}>
            Continue
          </button>
        </form>
      ) : (
        <form className="app-gate__panel" onSubmit={handleAliasSubmit}>
          <div className="app-gate__brand">BEN</div>
          <h1 className="app-gate__title">Auditor Sandbox</h1>
          <label className="app-gate__label" htmlFor="beta-alias">
            Enter your name or alias (e.g., Alon)
          </label>
          <input
            id="beta-alias"
            className="app-gate__input"
            type="text"
            autoComplete="nickname"
            autoFocus
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            placeholder="Alon"
            disabled={submitting}
          />
          <p className="app-gate__hint">Your projects and chat threads are isolated to this alias.</p>
          {error ? <p className="app-gate__error">{error}</p> : null}
          <button type="submit" className="app-gate__submit" disabled={submitting || !alias.trim()}>
            {submitting ? 'Starting sandbox…' : 'Enter Mission Control'}
          </button>
        </form>
      )}
    </div>
  )
}
