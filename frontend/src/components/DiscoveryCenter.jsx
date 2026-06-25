import { useCallback, useEffect, useMemo, useState } from 'react'
import PropTypes from 'prop-types'
import { connectPlatformCapability, togglePlatformCapability } from '../api/platformCapabilities.js'
import {
  DISCOVERY_CHANNELS,
  DISCOVERY_SECTIONS,
  getDiscoveryChannel,
} from '../data/discoveryCatalog.js'
import {
  getBrandTheme,
  resolveCapabilityActionLabel,
  resolveStatusPill,
  resolveToggleAriaLabel,
} from '../data/discoveryBrandTheme.js'
import { deriveActiveEngineCatalogKeys, findActiveFeatureForCatalog } from '../lib/globalFeatureCatalog.js'
import { DiscoveryBrandIcon } from './DiscoveryBrandIcon.jsx'
import './DiscoveryCenter.css'

function DiscoveryToggle({ active, loading, disabled, onToggle, ariaLabel, glow }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={active}
      aria-busy={loading}
      aria-label={ariaLabel}
      disabled={disabled || loading}
      onClick={onToggle}
      style={{ '--discovery-glow': glow }}
      className={[
        'discovery-toggle-track',
        active ? 'discovery-toggle-track--active' : '',
        disabled || loading ? 'discovery-toggle-track--disabled' : '',
      ].join(' ')}
    >
      <span
        className={[
          'discovery-toggle-thumb',
          active ? 'discovery-toggle-thumb--on' : '',
          loading ? 'discovery-toggle-thumb--loading' : '',
        ].join(' ')}
      />
    </button>
  )
}

DiscoveryToggle.propTypes = {
  active: PropTypes.bool.isRequired,
  loading: PropTypes.bool.isRequired,
  disabled: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
  ariaLabel: PropTypes.string.isRequired,
  glow: PropTypes.string.isRequired,
}

function StatusPill({ label, loading, active }) {
  const tone = loading ? 'enabling' : active ? 'live' : 'available'

  return (
    <span className={`discovery-status-pill discovery-status-pill--${tone}`} aria-live="polite">
      {label}
    </span>
  )
}

StatusPill.propTypes = {
  label: PropTypes.string.isRequired,
  loading: PropTypes.bool.isRequired,
  active: PropTypes.bool.isRequired,
}

function DiscoveryCard({ channel, active, loading, disabled, error, onToggle }) {
  const brand = getBrandTheme(channel.brandId)
  const statusLabel = resolveStatusPill({ loading, active, sectionId: channel.section })
  const actionLabel = resolveCapabilityActionLabel({ loading, active, sectionId: channel.section })
  const toggleAriaLabel = resolveToggleAriaLabel({
    active,
    title: channel.title,
    sectionId: channel.section,
  })
  const showSonarPulse = brand.sonarPulse && active && !loading

  return (
    <article
      className={['discovery-card', active ? 'discovery-card--active' : ''].filter(Boolean).join(' ')}
      style={{
        '--discovery-glow': brand.glow,
        '--discovery-gradient': brand.gradient || 'rgba(255, 255, 255, 0.06)',
      }}
      aria-live="polite"
    >
      {showSonarPulse ? (
        <span className="discovery-sonar-ring discovery-sonar-ring--active" aria-hidden="true" />
      ) : null}

      <div className="discovery-card__body">
        <div className="discovery-card__header">
          <div className="discovery-icon-shell">
            <DiscoveryBrandIcon
              brandId={channel.brandId}
              sonarVariant={channel.sonarVariant}
              active={active}
            />
          </div>
          <div className="discovery-card__copy">
            <div className="discovery-card__title-row">
              <h3 className="discovery-card__title">{channel.title}</h3>
              <StatusPill label={statusLabel} loading={loading} active={active} />
            </div>
            <p className="discovery-card__description">{channel.description}</p>
          </div>
        </div>
        {error ? <p className="discovery-card__error">{error}</p> : null}
      </div>

      <div className="discovery-card__footer">
        <span className="discovery-card__action-label">{actionLabel}</span>
        <DiscoveryToggle
          active={active}
          loading={loading}
          disabled={disabled}
          onToggle={onToggle}
          ariaLabel={toggleAriaLabel}
          glow={brand.glow}
        />
      </div>
    </article>
  )
}

DiscoveryCard.propTypes = {
  channel: PropTypes.shape({
    catalogKey: PropTypes.string.isRequired,
    brandId: PropTypes.string.isRequired,
    sonarVariant: PropTypes.string,
    section: PropTypes.oneOf(['compute', 'data', 'sonar']).isRequired,
    title: PropTypes.string.isRequired,
    description: PropTypes.string.isRequired,
  }).isRequired,
  active: PropTypes.bool.isRequired,
  loading: PropTypes.bool.isRequired,
  disabled: PropTypes.bool.isRequired,
  error: PropTypes.string,
  onToggle: PropTypes.func.isRequired,
}

function sectionActiveCount(sectionId, engines, integrations) {
  const sectionChannels = DISCOVERY_CHANNELS.filter((channel) => channel.section === sectionId)
  const engineKeys = new Set(engines.map((row) => String(row.catalog_key || '')))
  const integrationKeys = new Set(integrations.map((row) => String(row.catalog_key || '')))

  if (sectionId === 'compute') {
    return sectionChannels.filter((channel) => engineKeys.has(channel.catalogKey)).length
  }
  return sectionChannels.filter((channel) => integrationKeys.has(channel.catalogKey)).length
}

/** Sidebar trigger only — opens the full-screen catalog overlay. */
export function CapabilityCatalogTrigger({ onOpen, disabled = false }) {
  return (
    <button
      type="button"
      className="discovery-catalog-trigger"
      onClick={onOpen}
      disabled={disabled}
      aria-haspopup="dialog"
    >
      <span>Capability Catalog</span>
      <span className="discovery-catalog-trigger__chevron" aria-hidden="true">
        ▸
      </span>
    </button>
  )
}

CapabilityCatalogTrigger.propTypes = {
  onOpen: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
}

CapabilityCatalogTrigger.defaultProps = {
  disabled: false,
}

/** Full-screen platform capability registry overlay. */
export function DiscoveryCenterOverlay({
  open,
  onClose,
  buildHeaders,
  disabled = false,
  featureState,
  onFeaturesChange,
}) {
  const {
    snapshot,
    catalogKeySet,
    engines,
    integrations,
    activeFeatures,
    loading: loadingCatalog,
    reload,
    error: catalogError,
  } = featureState

  const [pendingKeys, setPendingKeys] = useState(() => new Set())
  const [optimisticActive, setOptimisticActive] = useState(() => new Map())
  const [errorsByKey, setErrorsByKey] = useState(() => ({}))

  useEffect(() => {
    if (open) void reload()
  }, [open, reload])

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  const notifyFeaturesChange = useCallback(
    (nextSnapshot) => {
      onFeaturesChange?.({
        catalogKeys: nextSnapshot.catalog_keys,
        engines: nextSnapshot.engines,
        integrations: nextSnapshot.integrations,
        activeFeatures: nextSnapshot.active_features,
      })
    },
    [onFeaturesChange]
  )

  const refreshFeatures = useCallback(async () => {
    const next = await reload()
    notifyFeaturesChange(next)
    return next
  }, [reload, notifyFeaturesChange])

  const channelsBySection = useMemo(() => {
    const grouped = {
      compute: [],
      data: [],
      sonar: [],
    }
    for (const channel of DISCOVERY_CHANNELS) {
      grouped[channel.section].push(channel)
    }
    return grouped
  }, [])

  const resolveActive = useCallback(
    (catalogKey, sectionId, optimisticMap = optimisticActive) => {
      if (optimisticMap.has(catalogKey)) {
        return optimisticMap.get(catalogKey)
      }
      if (!catalogKeySet.has(catalogKey)) return false
      const record = findActiveFeatureForCatalog(activeFeatures, catalogKey)
      if (!record) return false
      if (sectionId === 'compute') return record.channel_kind === 'engine'
      return record.channel_kind === 'integration'
    },
    [optimisticActive, catalogKeySet, activeFeatures]
  )

  const buildOptimisticCatalogKeys = useCallback(
    (optimisticMap) => {
      const isChannelActive = (catalogKey, sectionId) =>
        resolveActive(catalogKey, sectionId, optimisticMap)
      const engineKeys = deriveActiveEngineCatalogKeys(isChannelActive)
      const integrationKeys = DISCOVERY_CHANNELS.filter(
        (channel) =>
          channel.section !== 'compute' && isChannelActive(channel.catalogKey, channel.section)
      ).map((channel) => channel.catalogKey)
      return [...engineKeys, ...integrationKeys]
    },
    [resolveActive]
  )

  const setPending = useCallback((catalogKey, pending) => {
    setPendingKeys((prev) => {
      const next = new Set(prev)
      if (pending) next.add(catalogKey)
      else next.delete(catalogKey)
      return next
    })
  }, [])

  const handleToggle = useCallback(
    async (catalogKey) => {
      if (!buildHeaders || disabled) return
      const channel = getDiscoveryChannel(catalogKey)
      if (!channel) return

      const currentlyActive = resolveActive(catalogKey, channel.section)
      const nextActive = !currentlyActive

      setErrorsByKey((prev) => {
        const next = { ...prev }
        delete next[catalogKey]
        return next
      })
      setOptimisticActive((prev) => new Map(prev).set(catalogKey, nextActive))
      const nextOptimisticMap = new Map(optimisticActive).set(catalogKey, nextActive)
      notifyFeaturesChange({ catalogKeys: buildOptimisticCatalogKeys(nextOptimisticMap) })
      setPending(catalogKey, true)

      try {
        const headers = await buildHeaders()
        if (currentlyActive) {
          const record = findActiveFeatureForCatalog(activeFeatures, catalogKey)
          if (!record?.id) throw new Error('No active capability mapping found')
          await togglePlatformCapability(record.id, headers)
        } else {
          await connectPlatformCapability(channel.connect, headers)
        }
        await refreshFeatures()
        setOptimisticActive((prev) => {
          const next = new Map(prev)
          next.delete(catalogKey)
          return next
        })
      } catch (e) {
        setOptimisticActive((prev) => {
          const next = new Map(prev)
          next.delete(catalogKey)
          return next
        })
        setErrorsByKey((prev) => ({
          ...prev,
          [catalogKey]: e?.message || 'Capability toggle failed',
        }))
      } finally {
        setPending(catalogKey, false)
      }
    },
    [activeFeatures, buildHeaders, buildOptimisticCatalogKeys, disabled, notifyFeaturesChange, optimisticActive, refreshFeatures, resolveActive, setPending]
  )

  if (!open) return null

  return (
    <div className="discovery-overlay" role="dialog" aria-modal="true" aria-label="Capability catalog">
      <button
        type="button"
        className="discovery-overlay__scrim"
        aria-label="Close capability catalog"
        onClick={onClose}
      />
      <div className="discovery-overlay__panel">
        <header className="discovery-overlay__header">
          <div className="discovery-overlay__intro">
            <h2 className="discovery-overlay__title">Master Switchboard</h2>
            <p className="discovery-overlay__subtitle">
              Built-in capability catalog — free platform modules for every chat and workspace.
            </p>
            <p className="discovery-overlay__meta">
              {snapshot.total_active} active · {snapshot.total_configured} configured platform-wide
            </p>
          </div>
          <div className="discovery-overlay__header-actions">
            {loadingCatalog ? (
              <span className="discovery-status-pill discovery-status-pill--enabling" aria-live="polite">
                SYNCING
              </span>
            ) : null}
            <button type="button" className="discovery-overlay__close" onClick={onClose} aria-label="Close">
              ×
            </button>
          </div>
        </header>

        <div className="discovery-overlay__content">
          {catalogError ? <p className="discovery-overlay__error">{catalogError}</p> : null}

          {DISCOVERY_SECTIONS.map((section) => {
            const activeInSection = sectionActiveCount(section.id, engines, integrations)
            const sectionTotal = channelsBySection[section.id].length
            return (
              <section key={section.id} className="discovery-overlay__section">
                <div className="discovery-overlay__section-head">
                  <h3 className="discovery-overlay__section-title">{section.title}</h3>
                  <span className="discovery-overlay__section-meta" aria-live="polite">
                    {section.id === 'compute'
                      ? `${activeInSection} engine${activeInSection === 1 ? '' : 's'} enabled`
                      : `${activeInSection} channel${activeInSection === 1 ? '' : 's'} ready`}{' '}
                    · {sectionTotal} available
                  </span>
                </div>
                <div className="discovery-overlay__grid">
                  {channelsBySection[section.id].map((channel) => (
                    <DiscoveryCard
                      key={channel.catalogKey}
                      channel={channel}
                      active={Boolean(resolveActive(channel.catalogKey, section.id))}
                      loading={pendingKeys.has(channel.catalogKey)}
                      disabled={disabled}
                      error={errorsByKey[channel.catalogKey]}
                      onToggle={() => void handleToggle(channel.catalogKey)}
                    />
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      </div>
    </div>
  )
}

DiscoveryCenterOverlay.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  buildHeaders: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  featureState: PropTypes.shape({
    snapshot: PropTypes.object.isRequired,
    catalogKeySet: PropTypes.instanceOf(Set).isRequired,
    engines: PropTypes.array.isRequired,
    integrations: PropTypes.array.isRequired,
    activeFeatures: PropTypes.array.isRequired,
    loading: PropTypes.bool.isRequired,
    reload: PropTypes.func.isRequired,
    error: PropTypes.string,
  }).isRequired,
  onFeaturesChange: PropTypes.func,
}

DiscoveryCenterOverlay.defaultProps = {
  disabled: false,
  onFeaturesChange: undefined,
}
