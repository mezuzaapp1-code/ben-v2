import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { getSpeakingProviders } from '../src/providers/providerRegistry.js'
import { coerceRegisteredModel, formatModelShortLabel, getProviderModelOptions, getTier1Model } from '../src/providers/providerModelChoices.js'

const providers = getSpeakingProviders()
for (const id of ['gpt', 'claude', 'gemini', 'grok', 'deepseek']) {
  assert(providers.some((p) => p.id === id))
}
assert.equal(providers.find((p) => p.id === 'deepseek').label, 'DeepSeek')
assert.deepEqual(getProviderModelOptions('deepseek'), ['deepseek-flash', 'deepseek-v4-pro'])
assert.equal(getTier1Model('deepseek'), 'deepseek-flash')
assert.equal(coerceRegisteredModel('deepseek', 'gpt-4o'), 'deepseek-flash')
assert.equal(coerceRegisteredModel('deepseek', 'deepseek-v4-pro'), 'deepseek-v4-pro')
assert.equal(formatModelShortLabel('deepseek-flash'), 'flash')
assert.equal(formatModelShortLabel('deepseek-v4-pro'), 'v4-pro')
assert(getProviderModelOptions('gpt').includes('gpt-6-astra'))
const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
assert(app.includes('deepseek: selectedDeepSeekModel'))
assert(app.includes("providerId === 'deepseek') setSelectedDeepSeekModel(modelId)"))
assert(app.includes("providerId === 'deepseek') setSelectedDeepSeekModel(tier1)"))
console.log('DeepSeek menu, selection and existing provider checks passed')
