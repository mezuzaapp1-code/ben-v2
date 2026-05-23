import { getSpeakingProviders } from './providerRegistry.js'
import './ProviderToolbar.css'

/** Horizontal speaking-provider selector for /chat routing. */
export function ProviderToolbar({
  activeProviderId,
  onActiveProviderChange,
  onAddProviderClick,
  disabled = false,
}) {
  const providers = getSpeakingProviders()

  return (
    <div className="provider-toolbar" role="toolbar" aria-label="Speaking provider">
      <span className="provider-toolbar__label">Speaker</span>
      <div className="provider-toolbar__list" role="group" aria-label="Providers">
        {providers.map((provider) => {
          const isActive = provider.id === activeProviderId
          return (
            <button
              key={provider.id}
              type="button"
              className={
                isActive
                  ? 'provider-toolbar__chip provider-toolbar__chip--active'
                  : 'provider-toolbar__chip'
              }
              style={{ '--provider-accent': provider.accent }}
              aria-pressed={isActive}
              aria-label={`${provider.label}${isActive ? ' (active)' : ''}`}
              disabled={disabled}
              onClick={() => onActiveProviderChange(provider.id)}
            >
              <span className="provider-toolbar__dot" aria-hidden="true" />
              {provider.label}
            </button>
          )
        })}
      </div>
      <button
        type="button"
        className="provider-toolbar__add"
        aria-label="Add AI provider (coming soon)"
        title="Add AI provider (coming soon)"
        disabled={disabled}
        onClick={() => onAddProviderClick?.()}
      >
        + Add AI
      </button>
    </div>
  )
}
