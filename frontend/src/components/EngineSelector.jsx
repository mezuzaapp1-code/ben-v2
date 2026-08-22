import { getSpeakingProviders } from '../providers/providerRegistry.js'
import './EngineSelector.css'

/** Tier 1 engine family picker — speaking providers from the registry (no sub-model labels). */
export function EngineSelector({ activeProviderId, onActiveProviderChange, disabled = false }) {
  const providers = getSpeakingProviders()

  return (
    <div className="engine-selector" role="radiogroup" aria-label="Speaking engine">
      {providers.map((provider) => {
        const isActive = provider.id === activeProviderId
        return (
          <button
            key={provider.id}
            type="button"
            role="radio"
            aria-checked={isActive}
            className={isActive ? 'engine-selector__pill engine-selector__pill--active' : 'engine-selector__pill'}
            style={{ '--engine-accent': provider.accent }}
            disabled={disabled}
            onClick={() => onActiveProviderChange(provider.id)}
          >
            <span className="engine-selector__dot" aria-hidden="true" />
            <span className="engine-selector__label">{provider.label}</span>
          </button>
        )
      })}
    </div>
  )
}
