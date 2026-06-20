/**
 * Brand themes for Discovery Center capability cards (visual only — status copy is unified).
 * @typedef {'grok' | 'claude' | 'gemini' | 'gmail' | 'gdrive' | 'local' | 'library' | 'sonar'} BrandId
 * @typedef {'compute' | 'data' | 'sonar'} DiscoverySectionId
 * @typedef {Object} DiscoveryBrandTheme
 * @property {BrandId} id
 * @property {string} accent
 * @property {string} glow
 * @property {string} [gradient]
 * @property {boolean} [sonarPulse]
 */

/** @type {Record<BrandId, DiscoveryBrandTheme>} */
export const DISCOVERY_BRAND_THEMES = Object.freeze({
  grok: {
    id: 'grok',
    accent: '#0f0f14',
    glow: '#818cf8',
    gradient: 'linear-gradient(135deg, #0f0f14 0%, #1e1b4b 55%, #312e81 100%)',
  },
  claude: {
    id: 'claude',
    accent: '#c15f3c',
    glow: '#e8a088',
    gradient: 'linear-gradient(135deg, #3d2318 0%, #c15f3c 45%, #e8a088 100%)',
  },
  gemini: {
    id: 'gemini',
    accent: '#4285f4',
    glow: '#34a853',
    gradient: 'linear-gradient(135deg, #4285f4 0%, #34a853 50%, #fbbc04 100%)',
  },
  gmail: {
    id: 'gmail',
    accent: '#ea4335',
    glow: '#fbbc04',
    gradient: 'linear-gradient(135deg, #ea4335 0%, #fbbc04 50%, #34a853 100%)',
  },
  gdrive: {
    id: 'gdrive',
    accent: '#4285f4',
    glow: '#34a853',
    gradient: 'linear-gradient(135deg, #4285f4 0%, #0f9d58 40%, #f4b400 75%, #db4437 100%)',
  },
  local: {
    id: 'local',
    accent: '#64748b',
    glow: '#94a3b8',
  },
  library: {
    id: 'library',
    accent: '#6366f1',
    glow: '#a5b4fc',
  },
  sonar: {
    id: 'sonar',
    accent: '#22c55e',
    glow: '#4ade80',
    sonarPulse: true,
  },
})

/** @param {BrandId} brandId */
export function getBrandTheme(brandId) {
  return DISCOVERY_BRAND_THEMES[brandId] ?? DISCOVERY_BRAND_THEMES.library
}

/**
 * Unified capability-catalog status labels (free built-in modules — no commercial framing).
 * @param {{ loading: boolean, active: boolean, sectionId?: DiscoverySectionId }} params
 */
export function resolveStatusPill({ loading, active, sectionId = 'data' }) {
  if (loading) return 'ENABLING'
  if (active) return sectionId === 'compute' ? 'ACTIVE IN WORKSPACE' : 'READY'
  return 'AVAILABLE'
}

/**
 * Footer micro-copy beside each modular switch.
 * @param {{ loading: boolean, active: boolean, sectionId?: DiscoverySectionId }} params
 */
export function resolveCapabilityActionLabel({ loading, active, sectionId = 'data' }) {
  if (loading) {
    if (sectionId === 'compute') return 'Activating engine…'
    if (sectionId === 'sonar') return 'Enabling capability…'
    return 'Connecting channel…'
  }
  if (active) {
    if (sectionId === 'compute') return 'Engine active in workspace'
    if (sectionId === 'sonar') return 'Capability armed in workspace'
    return 'Channel ready in workspace'
  }
  if (sectionId === 'compute') return 'Activate Engine'
  if (sectionId === 'sonar') return 'Enable Capability'
  return 'Connect Channel'
}

/**
 * Accessible label for the on/off switch control.
 * @param {{ active: boolean, title: string, sectionId?: DiscoverySectionId }} params
 */
export function resolveToggleAriaLabel({ active, title, sectionId = 'data' }) {
  if (active) {
    if (sectionId === 'compute') return `Deactivate ${title} engine`
    return `Disconnect ${title} channel`
  }
  if (sectionId === 'compute') return `Activate ${title} engine`
  if (sectionId === 'sonar') return `Enable ${title} capability`
  return `Connect ${title} channel`
}
