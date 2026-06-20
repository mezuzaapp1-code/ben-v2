import { useCallback, useMemo, useState } from 'react'
import PropTypes from 'prop-types'
import {
  connectProjectRepository,
  toggleProjectRepository,
} from '../api/repositories.js'
import {
  DISCOVERY_CHANNELS,
  DISCOVERY_SECTIONS,
  getDiscoveryChannel,
} from '../data/discoveryCatalog.js'
import { getBrandTheme, resolveCapabilityActionLabel, resolveStatusPill, resolveToggleAriaLabel } from '../data/discoveryBrandTheme.js'
import { findActiveFeatureForCatalog } from '../lib/globalFeatureCatalog.js'
import { useProjectActiveFeatures } from '../hooks/useProjectActiveFeatures.js'
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
        active ? 'discovery-toggle-track--active border-emerald-500/60 bg-emerald-600/75' : 'border-ben-border bg-ben-elevated',
        disabled || loading ? 'cursor-not-allowed opacity-60' : '',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ben-accent',
      ].join(' ')}
    >
      <span
        className={[
          'discovery-toggle-thumb',
          active ? 'translate-x-[1.15rem]' : 'translate-x-0',
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
  const tone = loading
    ? 'discovery-status-pill--enabling bg-amber-500/15 text-amber-200'
    : active
      ? 'discovery-status-pill--live bg-emerald-500/20 text-emerald-300'
      : 'discovery-status-pill--available bg-white/5 text-ben-muted'

  return (
    <span className={`discovery-status-pill ${tone}`} aria-live="polite">
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
        '--discovery-gradient': brand.gradient || 'rgba(255,255,255,0.06)',
      }}
      aria-live="polite"
    >
      {showSonarPulse ? (
        <span className="discovery-sonar-ring discovery-sonar-ring--active" aria-hidden="true" />
      ) : null}

      <div className="relative min-h-[5.25rem] space-y-2">
        <div className="flex items-start gap-2.5">
          <div className="discovery-icon-shell">
            <DiscoveryBrandIcon
              brandId={channel.brandId}
              sonarVariant={channel.sonarVariant}
              active={active}
            />
          </div>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-sm font-semibold leading-snug text-ben-text">{channel.title}</h3>
              <StatusPill label={statusLabel} loading={loading} active={active} />
            </div>
            <p className="text-2xs leading-relaxed text-ben-muted">{channel.description}</p>
          </div>
        </div>
        {error ? <p className="text-2xs text-red-400">{error}</p> : null}
      </div>

      <div className="relative mt-3 flex min-h-8 items-center justify-between gap-2 border-t border-ben-border/80 pt-2">
        <span className="min-w-0 flex-1 text-2xs text-ben-muted">{actionLabel}</span>
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

export function DiscoveryCenter({ projectSlug, buildHeaders, disabled = false, onFeaturesChange }) {
  const [open, setOpen] = useState(false)
  const {
    snapshot,
    catalogKeySet,
    engines,
    integrations,
    activeFeatures,
    loading: loadingCatalog,
    reload,
  } = useProjectActiveFeatures(projectSlug, buildHeaders)

  const [pendingKeys, setPendingKeys] = useState(() => new Set())
  const [optimisticActive, setOptimisticActive] = useState(() => new Map())
  const [errorsByKey, setErrorsByKey] = useState(() => ({}))

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
    (catalogKey, sectionId) => {
      if (optimisticActive.has(catalogKey)) {
        return optimisticActive.get(catalogKey)
      }
      if (!catalogKeySet.has(catalogKey)) return false
      const record = findActiveFeatureForCatalog(activeFeatures, catalogKey)
      if (!record) return false
      if (sectionId === 'compute') return record.channel_kind === 'engine'
      return record.channel_kind === 'integration'
    },
    [optimisticActive, catalogKeySet, activeFeatures]
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
      if (!projectSlug || !buildHeaders || disabled) return
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
      setPending(catalogKey, true)

      try {
        const headers = await buildHeaders()
        if (currentlyActive) {
          const record = findActiveFeatureForCatalog(activeFeatures, catalogKey)
          if (!record?.id) throw new Error('No active channel mapping found')
          await toggleProjectRepository(projectSlug, record.id, headers)
        } else {
          await connectProjectRepository(projectSlug, channel.connect, headers)
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
          [catalogKey]: e?.message || 'Channel toggle failed',
        }))
      } finally {
        setPending(catalogKey, false)
      }
    },
    [
      activeFeatures,
      buildHeaders,
      disabled,
      projectSlug,
      refreshFeatures,
      resolveActive,
      setPending,
    ]
  )

  const hasProjectContext = Boolean(String(projectSlug || '').trim())

  return (
    <section
      className={`discovery-center-panel${open ? ' discovery-center-panel--open' : ''}`}
      aria-label="Capability catalog"
    >
      <button
        type="button"
        className="discovery-center-panel__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span>Capability Catalog</span>
        <span className="discovery-center-panel__chevron" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open ? (
        <div className="discovery-center-panel__body">
          {!hasProjectContext ? (
            <p className="discovery-center-panel__hint">
              Open or create a project workspace to enable built-in engines and data channels.
            </p>
          ) : (
            <>
              <header className="discovery-center-panel__catalog-header">
                <div>
                  <h2 className="discovery-center-panel__catalog-title">Master Switchboard</h2>
                  <p className="discovery-center-panel__catalog-subtitle">
                    Built-in capability catalog — free for every workspace. Toggle modules on demand.
                  </p>
                  <p className="text-2xs text-ben-muted">
                    {snapshot.total_active} active · {snapshot.total_configured} configured in org
                  </p>
                </div>
                {loadingCatalog ? (
                  <span className="discovery-status-pill discovery-status-pill--enabling bg-amber-500/15 text-amber-200" aria-live="polite">
                    SYNCING
                  </span>
                ) : null}
              </header>

              <div className="space-y-4">
                {DISCOVERY_SECTIONS.map((section) => {
                  const activeInSection = sectionActiveCount(section.id, engines, integrations)
                  const sectionTotal = channelsBySection[section.id].length
                  return (
                    <div key={section.id}>
                      <div className="mb-2 flex items-baseline justify-between gap-2">
                        <h3 className="text-2xs font-semibold uppercase tracking-wider text-ben-muted">
                          {section.title}
                        </h3>
                        <span className="text-2xs text-ben-muted" aria-live="polite">
                          {section.id === 'compute'
                            ? `${activeInSection} engine${activeInSection === 1 ? '' : 's'} enabled`
                            : `${activeInSection} channel${activeInSection === 1 ? '' : 's'} ready`}{' '}
                          · {sectionTotal} available
                        </span>
                      </div>
                      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                        {channelsBySection[section.id].map((channel) => (
                          <DiscoveryCard
                            key={channel.catalogKey}
                            channel={channel}
                            active={Boolean(resolveActive(channel.catalogKey, section.id))}
                            loading={pendingKeys.has(channel.catalogKey)}
                            disabled={disabled || loadingCatalog}
                            error={errorsByKey[channel.catalogKey]}
                            onToggle={() => void handleToggle(channel.catalogKey)}
                          />
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      ) : null}
    </section>
  )
}

DiscoveryCenter.propTypes = {
  projectSlug: PropTypes.string,
  buildHeaders: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  onFeaturesChange: PropTypes.func,
}

DiscoveryCenter.defaultProps = {
  projectSlug: null,
  disabled: false,
  onFeaturesChange: undefined,
}
