/** Maps composer speaking providers to global Discovery Center engine catalog keys. */
export const PROVIDER_ENGINE_CATALOG_KEYS = Object.freeze({
  gpt: 'engine-grok',
  claude: 'engine-claude',
  gemini: 'engine-gemini',
})

/** @param {string} providerId */
export function getProviderCatalogKey(providerId) {
  return PROVIDER_ENGINE_CATALOG_KEYS[String(providerId || '').trim()] ?? null
}

/** @param {readonly string[] | string[]} catalogKeys */
export function buildActiveCatalogKeySet(catalogKeys) {
  return new Set((catalogKeys || []).map((key) => String(key || '').trim()).filter(Boolean))
}

/**
 * @param {readonly string[] | string[] | undefined} catalogKeys
 * @param {string} providerId
 * @param {{ gateWhenEmpty?: boolean }} [options]
 */
export function isProviderGloballyActive(catalogKeys, providerId, { gateWhenEmpty = false } = {}) {
  const catalogKey = getProviderCatalogKey(providerId)
  if (!catalogKey) return true
  const keys = catalogKeys || []
  if (keys.length === 0) return !gateWhenEmpty
  return buildActiveCatalogKeySet(keys).has(catalogKey)
}

/** @param {readonly string[]} catalogKeys */
export function listActiveSpeakingProviderIds(catalogKeys) {
  return Object.keys(PROVIDER_ENGINE_CATALOG_KEYS).filter((providerId) =>
    isProviderGloballyActive(catalogKeys, providerId, { gateWhenEmpty: false })
  )
}

/**
 * Build compute-engine catalog keys from per-channel active resolution.
 * @param {(catalogKey: string, sectionId: string) => boolean} isChannelActive
 */
export function deriveActiveEngineCatalogKeys(isChannelActive) {
  return Object.values(PROVIDER_ENGINE_CATALOG_KEYS).filter((catalogKey) =>
    Boolean(isChannelActive(catalogKey, 'compute'))
  )
}

/**
 * @param {Array<{ id: number, catalog_key?: string, status?: string, channel_kind?: string, source_metadata?: Record<string, unknown> }>} activeFeatures
 * @param {string} catalogKey
 */
export function findActiveFeatureForCatalog(activeFeatures, catalogKey) {
  const token = String(catalogKey || '').trim()
  if (!token) return null
  return (
    (activeFeatures || []).find((row) => {
      if (row.status && row.status !== 'active') return false
      const rowKey = String(row.catalog_key || row.source_metadata?.catalog_key || '').trim()
      return rowKey === token
    }) ?? null
  )
}

/**
 * @param {'engine' | 'integration'} channelKind
 * @param {Array<{ catalog_key?: string, channel_kind?: string, status?: string }>} rows
 */
export function countActiveFeaturesByKind(channelKind, rows) {
  return (rows || []).filter(
    (row) =>
      row.status !== 'disconnected' &&
      String(row.channel_kind || '') === channelKind &&
      Boolean(row.catalog_key)
  ).length
}
