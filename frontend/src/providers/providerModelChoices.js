/** Explicit provider model choices — canonical BEN ids from shared/frontier_models.json. */

import frontierModels from '../../../shared/frontier_models.json' with { type: 'json' }

function catalogGeminiModels() {
  const google = frontierModels?.providers?.google
  const ids = []
  const fast = String(google?.fast || '').trim()
  if (fast) ids.push(fast)
  if (Array.isArray(google?.legacy)) {
    for (const raw of google.legacy) {
      const id = String(raw || '').trim()
      if (id && !ids.includes(id)) ids.push(id)
    }
  }
  return ids.filter((id) => id && !id.startsWith('gemini-1.5-'))
}

/** Tier 1 flagship defaults for operational routing (must match services/tier1_models.py). */
export const TIER1_PROVIDER_MODELS = Object.freeze({
  gpt: 'gpt-4o',
  claude: 'claude-opus-4.8',
  gemini: String(frontierModels?.providers?.google?.fast || 'gemini-3.8-flash'),
  grok: 'grok-4.6',
})

const GPT_BASE_MODELS = ['gpt-4o', 'gpt-4o-mini', 'gpt-5.5-instant', 'gpt-5.5-pro']

function catalogGptExtras() {
  const identities = frontierModels?.identities
  if (!identities || typeof identities !== 'object') return []
  const extras = []
  for (const spec of Object.values(identities)) {
    if (!spec || spec.speaking_provider !== 'gpt') continue
    const id = String(spec.api_model || '').trim()
    if (id && !GPT_BASE_MODELS.includes(id) && !extras.includes(id)) extras.push(id)
  }
  return extras
}

/** @type {Record<string, readonly string[]>} */
export const PROVIDER_MODEL_OPTIONS = Object.freeze({
  gpt: Object.freeze([...GPT_BASE_MODELS, ...catalogGptExtras()]),
  claude: Object.freeze(['claude-opus-4.8', 'claude-sonnet-4.6', 'claude-sonnet-4-6']),
  gemini: Object.freeze(catalogGeminiModels()),
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

function catalogDisplayName(modelId) {
  const id = String(modelId || '').trim()
  if (!id) return ''
  const identities = frontierModels?.identities
  if (!identities || typeof identities !== 'object') return ''
  for (const [key, spec] of Object.entries(identities)) {
    if (!spec || typeof spec !== 'object') continue
    const apiId = String(spec.api_model || '').trim()
    if (apiId === id || String(key).split(':')[1] === id) {
      return String(spec.display_name || '').trim()
    }
  }
  return ''
}

/** Menu/settings label. Named catalog identities (e.g. Astra) override the raw id. */
export function getModelMenuLabel(modelId) {
  const id = String(modelId || '').trim()
  return catalogDisplayName(id) || id
}

/** Compact label shown inside provider pill (e.g. gpt-4o-mini → 4o-mini). */
export function formatModelShortLabel(modelId) {
  const id = String(modelId || '').trim()
  if (!id) return ''
  const named = catalogDisplayName(id)
  if (named) return named
  return id
    .replace(/^gpt-/i, '')
    .replace(/^claude-/i, '')
    .replace(/^gemini-/i, '')
    .replace(/^grok-/i, '')
    .replace(/-latest$/i, '')
    .replace(/-\d{8}$/i, '')
}
