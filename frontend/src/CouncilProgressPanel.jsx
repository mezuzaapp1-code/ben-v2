import {
  COUNCIL_LONG_PROMPT_HINT,
  COUNCIL_PHASE_LABELS,
  COUNCIL_PHASE_ORDER,
} from './councilProgress.js'

export function CouncilProgressPanel({ activePhase, longPrompt }) {
  if (!activePhase) return null
  const activeIdx = COUNCIL_PHASE_ORDER.indexOf(activePhase)
  return (
    <div className="council-progress-panel" role="status" aria-live="polite" aria-busy="true">
      {longPrompt ? <p className="council-long-hint">{COUNCIL_LONG_PROMPT_HINT}</p> : null}
      <ol className="council-progress-steps">
        {COUNCIL_PHASE_ORDER.map((phase, i) => {
          let state = 'pending'
          if (i < activeIdx) state = 'done'
          else if (i === activeIdx) state = 'active'
          return (
            <li key={phase} className={`council-step council-step--${state}`}>
              <span className="council-step-marker" aria-hidden="true">
                {state === 'done' ? '✓' : state === 'active' ? '…' : '○'}
              </span>
              <span className="council-step-label">{COUNCIL_PHASE_LABELS[phase]}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
