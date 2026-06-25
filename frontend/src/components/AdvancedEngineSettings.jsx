import { BasaltSelect } from './ui/BasaltSelect.jsx'
import { getSpeakingProviders } from '../providers/providerRegistry.js'
import { getProviderModelOptions, getTier1Model } from '../providers/providerModelChoices.js'
import { getProviderCatalogKey, isProviderGloballyActive } from '../lib/globalFeatureCatalog.js'
import './AdvancedEngineSettings.css'

/** Engine + tier configuration — single source of truth in the composer gear popover. */
export function EngineSettingsPanel({
  activeProviderId,
  onProviderChange,
  providerModels,
  onProviderModelChange,
  tier,
  onTierChange,
  tierOptions,
  disabled = false,
  compact = false,
  activeCatalogKeys = null,
  gateProviders = false,
}) {
  const providers = getSpeakingProviders()
  const catalogKeys = activeCatalogKeys ?? []

  const modelOptionsFor = (providerId) =>
    getProviderModelOptions(providerId).map((modelId) => ({
      value: modelId,
      label: modelId === getTier1Model(providerId) ? `${modelId} (Tier 1)` : modelId,
    }))

  const providerAvailability = (providerId) => {
    if (!gateProviders) {
      return { available: true, catalogKey: getProviderCatalogKey(providerId) }
    }
    const catalogKey = getProviderCatalogKey(providerId)
    const available = isProviderGloballyActive(catalogKeys, providerId)
    return { available, catalogKey }
  }

  return (
    <div className={`engine-settings-panel${compact ? ' engine-settings-panel--compact' : ''}`}>
      {!compact ? (
        <p className="engine-settings-panel__hint">
          {gateProviders
            ? 'Engines require an active Capability Catalog switchboard toggle.'
            : 'Cost-optimized overrides. Invoke other engines in chat — e.g. &quot;Hey Claude&quot;.'}
        </p>
      ) : null}
      <div className="engine-settings-panel__engines" role="radiogroup" aria-label="Active engine">
        {providers.map((provider) => {
          const isActive = provider.id === activeProviderId
          const { available, catalogKey } = providerAvailability(provider.id)
          const engineDisabled = disabled || !available
          return (
            <button
              key={provider.id}
              type="button"
              role="radio"
              aria-checked={isActive}
              aria-disabled={engineDisabled}
              title={
                available
                  ? undefined
                  : `Activate ${catalogKey || provider.label} in the Capability Catalog to enable this engine`
              }
              className={[
                'engine-settings-panel__engine',
                isActive ? 'engine-settings-panel__engine--active' : '',
                !available ? 'engine-settings-panel__engine--gated' : '',
              ].join(' ')}
              style={{ '--engine-accent': provider.accent }}
              disabled={engineDisabled}
              onClick={() => {
                if (!available) return
                onProviderChange?.(provider.id)
              }}
            >
              <span className="engine-settings-panel__engine-dot" aria-hidden="true" />
              {provider.shortLabel ?? provider.label}
              {!available ? (
                <span className="engine-settings-panel__engine-lock" aria-hidden="true">
                  🔒
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
      {providers.map((provider) => {
        const { available } = providerAvailability(provider.id)
        return (
          <BasaltSelect
            key={provider.id}
            className="engine-settings-panel__select"
            label={provider.label}
            value={providerModels[provider.id] ?? getTier1Model(provider.id)}
            onChange={(modelId) => onProviderModelChange(provider.id, modelId)}
            options={modelOptionsFor(provider.id)}
            disabled={disabled || !available}
            size="sm"
            aria-label={`${provider.label} model override`}
          />
        )
      })}
      <BasaltSelect
        className="engine-settings-panel__select"
        label="Tier"
        value={tier}
        onChange={onTierChange}
        options={tierOptions}
        disabled={disabled}
        size="sm"
        aria-label="Tier"
      />
    </div>
  )
}
