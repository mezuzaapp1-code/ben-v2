import { getSpeakingProviderById } from './providerRegistry.js'

/**
 * Human-readable provider + model line for chat assistant bubbles.
 * Falls back to model_used only, then empty (caller may show "Assistant").
 */
export function formatChatAssistantMeta(message) {
  const model = String(message?.model_used ?? '').trim()
  const providerId = String(message?.provider_id ?? '').trim()
  const label = getSpeakingProviderById(providerId)?.label ?? ''
  if (label && model) return `${label} · ${model}`
  if (label) return label
  return model
}
