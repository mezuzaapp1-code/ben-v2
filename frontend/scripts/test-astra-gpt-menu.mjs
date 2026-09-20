/**
 * Astra Gate 1 — Astra is a GPT model label, not a new speaking provider.
 * Run: node frontend/scripts/test-astra-gpt-menu.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { getSpeakingProviders, isSpeakingProviderId } from '../src/providers/providerRegistry.js'
import {
  DEFAULT_PROVIDER_MODELS,
  TIER1_PROVIDER_MODELS,
  coerceRegisteredModel,
  formatModelShortLabel,
  getModelMenuLabel,
  getProviderModelOptions,
  getTier1Model,
} from '../src/providers/providerModelChoices.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const ids = getSpeakingProviders().map((p) => p.id)
assert(ids.join(',') === 'gpt,claude,gemini,grok', 'speaking providers unchanged')
assert(!ids.includes('astra'), 'Astra is not a speaking provider')
assert(!isSpeakingProviderId('astra'), 'astra is not a valid speaking id')
assert(getSpeakingProviders().find((p) => p.id === 'gpt')?.label === 'GPT', 'GPT pill label unchanged')

const gptModels = getProviderModelOptions('gpt')
assert(
  JSON.stringify(gptModels) ===
    JSON.stringify(['gpt-4o', 'gpt-4o-mini', 'gpt-5.5-instant', 'gpt-5.5-pro', 'gpt-6-astra']),
  'GPT menu is existing models plus Astra'
)
assert(getModelMenuLabel('gpt-6-astra') === 'Astra', 'menu label is Astra')
assert(formatModelShortLabel('gpt-6-astra') === 'Astra', 'pill label is Astra')
assert(getModelMenuLabel('gpt-4o') === 'gpt-4o', 'gpt-4o menu label unchanged')
assert(formatModelShortLabel('gpt-4o-mini') === '4o-mini', 'gpt-4o-mini short label unchanged')
assert(coerceRegisteredModel('gpt', 'gpt-6-astra') === 'gpt-6-astra', 'GPT accepts gpt-6-astra')
assert(coerceRegisteredModel('gpt', 'unknown-gpt') === 'gpt-4o', 'unknown GPT coerces to Tier 1')
assert(getTier1Model('gpt') === 'gpt-4o', 'default GPT is still gpt-4o')
assert(TIER1_PROVIDER_MODELS.gpt === 'gpt-4o', 'Tier 1 GPT unchanged')
assert(DEFAULT_PROVIDER_MODELS.gpt === 'gpt-4o', 'default map GPT unchanged')
assert(coerceRegisteredModel('gpt', 'gpt-4o-mini') === 'gpt-4o-mini', 'existing GPT selection unchanged')

assert(
  JSON.stringify(getProviderModelOptions('claude')) ===
    JSON.stringify(['claude-opus-4.8', 'claude-sonnet-4.6', 'claude-sonnet-4-6']),
  'Claude list unchanged'
)
assert(
  JSON.stringify(getProviderModelOptions('gemini')) ===
    JSON.stringify(['gemini-3.8-flash', 'gemini-3.5-flash', 'gemini-2.5-flash']),
  'Gemini menu is current Flash ids without 1.5'
)
assert(JSON.stringify(getProviderModelOptions('grok')) === JSON.stringify(['grok-4.6', 'grok-4.3']), 'Grok list unchanged')
assert(getTier1Model('claude') === 'claude-opus-4.8', 'Claude default unchanged')
assert(getTier1Model('gemini') === 'gemini-3.8-flash', 'Gemini default is current Flash')
assert(!getProviderModelOptions('gemini').includes('gemini-1.5-flash'), 'gemini-1.5-flash is not selectable')
assert(coerceRegisteredModel('gemini', 'gemini-1.5-flash') === 'gemini-3.8-flash', 'retired Gemini coerces to current Flash')
assert(getTier1Model('grok') === 'grok-4.6', 'Grok default unchanged')

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
assert(app.includes('selectedGptModel'), 'Astra uses GPT model state, not a new store')
assert(!app.includes('selectedAstra'), 'no dedicated Astra provider state')
assert(app.includes('modelOverride: activeModelOverride'), 'chat request sends selected GPT model')
assert(app.includes("providerId: activeSpeakingProviderId"), 'chat request sends speaking provider')
assert(
  coerceRegisteredModel('gpt', 'gpt-6-astra') === 'gpt-6-astra',
  'refresh/coerce does not replace Astra with gpt-4o'
)

const panel = readFileSync(join(root, 'src/components/AdvancedEngineSettings.jsx'), 'utf8')
assert(panel.includes('getModelMenuLabel'), 'GPT dropdown shows catalog display names')
assert(panel.includes("label={provider.label}"), 'dropdowns stay under speaking-provider labels')

console.log('PASS: Astra is selectable under GPT as Astra; other providers unchanged')
