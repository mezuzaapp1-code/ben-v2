/** Explicit provider model choices — canonical BEN ids from shared/frontier_models.json. */



/** Tier 1 flagship defaults for operational routing (must match services/tier1_models.py). */

export const TIER1_PROVIDER_MODELS = Object.freeze({

  gpt: 'gpt-4o',

  claude: 'claude-opus-4.8',

  gemini: 'gemini-3.5-flash',

  grok: 'grok-4.6',

})



/** @type {Record<string, readonly string[]>} */

export const PROVIDER_MODEL_OPTIONS = Object.freeze({

  gpt: Object.freeze(['gpt-4o', 'gpt-4o-mini', 'gpt-5.5-instant', 'gpt-5.5-pro']),

  claude: Object.freeze(['claude-opus-4.8', 'claude-sonnet-4.6', 'claude-sonnet-4-6']),

  gemini: Object.freeze(['gemini-3.5-flash', 'gemini-2.5-flash', 'gemini-1.5-flash']),

  grok: Object.freeze(['grok-4.6', 'grok-4.3']),

})



/** @type {Record<string, string>} */

export const DEFAULT_PROVIDER_MODELS = Object.freeze({ ...TIER1_PROVIDER_MODELS })



/** @param {string} providerId */

export function getProviderModelOptions(providerId) {

  return PROVIDER_MODEL_OPTIONS[providerId] ?? []

}



/** @param {string} providerId */

export function getTier1Model(providerId) {

  return TIER1_PROVIDER_MODELS[providerId] ?? getProviderModelOptions(providerId)[0] ?? ''

}



/** Map stale/unknown UI model ids back to a registered Tier 1 default. */

export function coerceRegisteredModel(providerId, modelId) {

  const options = getProviderModelOptions(providerId)

  const candidate = String(modelId || '').trim()

  if (candidate && options.includes(candidate)) return candidate

  return getTier1Model(providerId)

}



/** Compact label shown inside provider pill (e.g. gpt-4o-mini → 4o-mini). */

export function formatModelShortLabel(modelId) {

  const id = String(modelId || '').trim()

  if (!id) return ''

  return id

    .replace(/^gpt-/i, '')

    .replace(/^claude-/i, '')

    .replace(/^gemini-/i, '')

    .replace(/^grok-/i, '')

    .replace(/-latest$/i, '')

    .replace(/-\d{8}$/i, '')

}

