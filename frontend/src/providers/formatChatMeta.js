import { getSpeakingProviderById } from './providerRegistry.js'

function appendPerfSegments(segments, message) {
  const ttft = message?.ttft_ms
  if (ttft != null && Number.isFinite(Number(ttft))) {
    segments.push(`TTFT: ${Math.round(Number(ttft))}ms`)
  }
  const tps = message?.tps
  if (tps != null && Number.isFinite(Number(tps))) {
    segments.push(`${Math.round(Number(tps))} TPS`)
  }
}

/**
 * Human-readable provider + model line for chat assistant bubbles.
 * Falls back to model_used only, then empty (caller may show "Assistant").
 */
export function formatChatAssistantMeta(message) {
  const model = String(message?.model_used ?? '').trim()
  const providerId = String(message?.provider_id ?? '').trim()
  const label = getSpeakingProviderById(providerId)?.label ?? ''
  const segments = []
  if (label && model) segments.push(`${label} · ${model}`)
  else if (label) segments.push(label)
  else if (model) segments.push(model)
  appendPerfSegments(segments, message)
  return segments.join(' · ')
}
