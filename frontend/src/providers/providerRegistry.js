/**
 * Speaking-provider registry (UI foundation).
 * Backend routing is not wired yet — selection is client state only.
 */

/** @typedef {{ id: string, label: string, shortLabel?: string, accent?: string }} SpeakingProvider */

/** @type {readonly SpeakingProvider[]} */
export const SPEAKING_PROVIDERS = Object.freeze([
  { id: 'gpt', label: 'GPT', shortLabel: 'GPT', accent: '#10a37f' },
  { id: 'claude', label: 'Claude', shortLabel: 'Claude', accent: '#d97757' },
  { id: 'gemini', label: 'Gemini', shortLabel: 'Gemini', accent: '#4285f4' },
])

export const DEFAULT_SPEAKING_PROVIDER_ID = 'gpt'

const PROVIDER_BY_ID = new Map(SPEAKING_PROVIDERS.map((p) => [p.id, p]))

/** @returns {readonly SpeakingProvider[]} */
export function getSpeakingProviders() {
  return SPEAKING_PROVIDERS
}

/** @param {string} id */
export function getSpeakingProviderById(id) {
  return PROVIDER_BY_ID.get(id) ?? null
}

/** @param {string} id */
export function isSpeakingProviderId(id) {
  return PROVIDER_BY_ID.has(id)
}
