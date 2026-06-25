/**
 * Netflix-style discovery catalog — maps UI channels to repository connect payloads.
 * @typedef {'compute' | 'data' | 'sonar'} DiscoverySectionId
 * @typedef {'grok' | 'claude' | 'gemini' | 'gmail' | 'gdrive' | 'local' | 'library' | 'sonar'} BrandId
 * @typedef {Object} DiscoveryConnectPayload
 * @property {string} name
 * @property {'local' | 'google_drive' | 'external_library'} source_type
 * @property {Record<string, unknown>} source_metadata
 * @typedef {Object} DiscoveryChannel
 * @property {string} catalogKey
 * @property {DiscoverySectionId} section
 * @property {BrandId} brandId
 * @property {string} [sonarVariant]
 * @property {string} title
 * @property {string} description
 * @property {DiscoveryConnectPayload} connect
 */

/** @type {readonly DiscoveryChannel[]} */
export const DISCOVERY_CHANNELS = Object.freeze([
  {
    catalogKey: 'engine-grok',
    section: 'compute',
    brandId: 'grok',
    title: 'Grok Compute Grid',
    description: 'Deep dark tech lane — neutral grid routing for fast tactical inference.',
    connect: {
      name: 'Grok Compute Grid',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'engine-grok', channel: 'grok', tier: 'fast' },
    },
  },
  {
    catalogKey: 'engine-claude',
    section: 'compute',
    brandId: 'claude',
    title: 'Claude Reasoning Core',
    description: 'Anthropic-grade reasoning head for architecture synthesis and council prep.',
    connect: {
      name: 'Claude Compute Engine',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'engine-claude', channel: 'claude', tier: 'reasoning' },
    },
  },
  {
    catalogKey: 'engine-gemini',
    section: 'compute',
    brandId: 'gemini',
    title: 'Gemini Multimodal',
    description: 'Multimodal engine channel for vision, docs, and field capture flows.',
    connect: {
      name: 'Gemini Compute Engine',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'engine-gemini', channel: 'gemini', tier: 'multimodal' },
    },
  },
  {
    catalogKey: 'repo-local',
    section: 'data',
    brandId: 'local',
    title: 'Local Repository Vault',
    description: 'Chunked ingestion for PDFs, logs, and digital books up to 500MB per file.',
    connect: {
      name: 'Local Repository Vault',
      source_type: 'local',
      source_metadata: { catalog_key: 'repo-local', ingest_mode: 'chunked_stream' },
    },
  },
  {
    catalogKey: 'repo-gmail',
    section: 'data',
    brandId: 'gmail',
    title: 'Gmail Live Stream',
    description: 'Recognizable inbox channel — metadata sync for operational mail threads.',
    connect: {
      name: 'Gmail Live Stream',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'repo-gmail', sync_mode: 'metadata_only', provider: 'gmail' },
    },
  },
  {
    catalogKey: 'repo-gdrive',
    section: 'data',
    brandId: 'gdrive',
    title: 'Google Drive Sync',
    description: 'Cloud folder mapping — metadata sync without immediate bulk transfer.',
    connect: {
      name: 'Google Drive Sync',
      source_type: 'google_drive',
      source_metadata: { catalog_key: 'repo-gdrive', sync_mode: 'metadata_only' },
    },
  },
  {
    catalogKey: 'repo-external-library',
    section: 'data',
    brandId: 'library',
    title: 'External Data Library',
    description: 'Catalog bridge for third-party datasets and reference corpora.',
    connect: {
      name: 'External Data Library',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'repo-external-library', sync_mode: 'catalog_bridge' },
    },
  },
  {
    catalogKey: 'sonar-token-scrub',
    section: 'sonar',
    brandId: 'sonar',
    sonarVariant: 'shield',
    title: 'Token Scrub Kill-Switch',
    description: 'Instantly disconnects cloud sources and scrubs OAuth tokens from memory.',
    connect: {
      name: 'Sovereign Token Scrub',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'sonar-token-scrub', capability: 'token_scrub' },
    },
  },
  {
    catalogKey: 'sonar-attention-xray',
    section: 'sonar',
    brandId: 'sonar',
    sonarVariant: 'radar',
    title: 'Hybrid Attention X-Ray',
    description: 'Semantic + recency + FTS5 focus weights with live sovereign radar sweep.',
    connect: {
      name: 'Hybrid Attention X-Ray',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'sonar-attention-xray', capability: 'attention_xray' },
    },
  },
  {
    catalogKey: 'sonar-tenant-isolation',
    section: 'sonar',
    brandId: 'sonar',
    sonarVariant: 'scan',
    title: 'Tenant Isolation Guard',
    description: 'Org-scoped repository boundaries with shielded disconnected defaults.',
    connect: {
      name: 'Tenant Isolation Guard',
      source_type: 'external_library',
      source_metadata: { catalog_key: 'sonar-tenant-isolation', capability: 'tenant_guard' },
    },
  },
])

/** @type {readonly { id: DiscoverySectionId, title: string }[]} */
export const DISCOVERY_SECTIONS = Object.freeze([
  { id: 'compute', title: 'Compute Engines' },
  { id: 'data', title: 'Data & Live Streams' },
  { id: 'sonar', title: 'Sovereign Sonar Security' },
])

/** @param {string} catalogKey */
export function getDiscoveryChannel(catalogKey) {
  return DISCOVERY_CHANNELS.find((channel) => channel.catalogKey === catalogKey) ?? null
}

/**
 * @param {Array<{ id: number, status: string, source_metadata?: Record<string, unknown> }>} repositories
 * @param {string} catalogKey
 */
export function findRepositoryForCatalog(repositories, catalogKey) {
  return (
    repositories.find(
      (repo) =>
        repo.status === 'active' &&
        String(repo.source_metadata?.catalog_key || '') === catalogKey
    ) ?? null
  )
}
