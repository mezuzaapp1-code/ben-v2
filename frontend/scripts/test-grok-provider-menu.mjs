/**
 * Grok V1 — existing provider/model settings panel includes GROK + model dropdown.
 * Run: node frontend/scripts/test-grok-provider-menu.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { getSpeakingProviders, isSpeakingProviderId } from '../src/providers/providerRegistry.js'
import {
  DEFAULT_PROVIDER_MODELS,
  TIER1_PROVIDER_MODELS,
  coerceRegisteredModel,
  getProviderModelOptions,
  getTier1Model,
} from '../src/providers/providerModelChoices.js'
import { PROVIDER_ENGINE_CATALOG_KEYS } from '../src/lib/globalFeatureCatalog.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const ids = getSpeakingProviders().map((p) => p.id)
assert(ids.includes('gpt') && ids.includes('claude') && ids.includes('gemini'), 'existing providers remain')
assert(ids.includes('grok'), 'Grok is a speaking provider')
assert(isSpeakingProviderId('grok'), 'grok is a valid speaking id')
assert(getSpeakingProviders().find((p) => p.id === 'grok')?.label === 'Grok', 'label Grok')

assert(JSON.stringify(getProviderModelOptions('grok')) === JSON.stringify(['grok-4.6', 'grok-4.3']), 'V1 Grok models')
assert(getTier1Model('grok') === 'grok-4.6', 'default is grok-4.6')
assert(TIER1_PROVIDER_MODELS.grok === 'grok-4.6', 'Tier 1 grok-4.6')
assert(DEFAULT_PROVIDER_MODELS.grok === 'grok-4.6', 'default map grok-4.6')
assert(coerceRegisteredModel('grok', 'grok-4.3') === 'grok-4.3', 'manual lower-cost selection')
assert(coerceRegisteredModel('grok', 'grok-4.20-multi-agent-0309') === 'grok-4.6', 'unknown grok model coerces to default')
assert(coerceRegisteredModel('gpt', 'gpt-4o-mini') === 'gpt-4o-mini', 'GPT dropdown unchanged')
assert(coerceRegisteredModel('claude', 'claude-sonnet-4.6') === 'claude-sonnet-4.6', 'Claude dropdown unchanged')
assert(coerceRegisteredModel('gemini', 'gemini-2.5-flash') === 'gemini-2.5-flash', 'Gemini dropdown unchanged')

assert(PROVIDER_ENGINE_CATALOG_KEYS.gpt === 'engine-grok', 'GPT catalog alias unchanged')
assert(PROVIDER_ENGINE_CATALOG_KEYS.grok === undefined, 'speaking grok is not engine-grok')

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
assert(app.includes('selectedGrokModel'), 'App holds Grok model state like other providers')
assert(app.includes("providerId === 'grok'"), 'Grok model change wired like GPT/Claude/Gemini')

const panel = readFileSync(join(root, 'src/components/AdvancedEngineSettings.jsx'), 'utf8')
assert(panel.includes('getSpeakingProviders()'), 'settings panel still maps registry providers')
assert(panel.includes('onProviderModelChange(provider.id, modelId)'), 'per-provider model dropdown unchanged')
assert(!panel.includes('primaryProviders'), 'no primary-provider special case')
assert((panel.match(/providers\.map\(/g) || []).length >= 2, 'engines and model rows share the same providers list')

const panelCss = readFileSync(join(root, 'src/components/AdvancedEngineSettings.css'), 'utf8')
assert(!panelCss.includes('33.333%'), 'engine pills are not locked to a 3-column wrap')
assert(/flex:\s*1 1 0/.test(panelCss), 'engine pills share one row equally')
assert(ids.join(',') === 'gpt,claude,gemini,grok', 'ENGINE order is GPT Claude Gemini Grok')

const selector = readFileSync(join(root, 'src/components/EngineSelector.jsx'), 'utf8')
assert(selector.includes('getSpeakingProviders()'), 'EngineSelector is data-driven')
assert(!selector.includes('primaryProviders'), 'EngineSelector has no primary-provider split')
const selectorCss = readFileSync(join(root, 'src/components/EngineSelector.css'), 'utf8')
assert(!selectorCss.includes('33.333%'), 'EngineSelector pills are not locked to a 3-column wrap')

const adapter = readFileSync(join(root, '../services/providers/xai_provider.py'), 'utf8')
assert(!adapter.includes('model = "grok-4.6"'), 'adapter does not hardcode the selected model')
assert(!adapter.includes('"search_parameters"'), 'adapter does not send search_parameters')
assert(!adapter.includes('"web_search_options"'), 'adapter does not send web_search_options')
assert(!adapter.includes('"tools"'), 'adapter does not send tools')

console.log('PASS: Grok provider/model menu uses the existing selector architecture')
