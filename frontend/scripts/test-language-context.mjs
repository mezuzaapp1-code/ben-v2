/**
 * Node smoke for languageContext + cognitiveLabels (mirrors Python contracts).
 * Run: node frontend/scripts/test-language-context.mjs
 */
import { detectDominantLanguage, isMeaningfulReasoningValue } from '../src/languageContext.js'
import { buildSynthesisBubbleText, councilPhaseTimers, expertStatusLabel, t } from '../src/cognitiveLabels.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const he = detectDominantLanguage('מה דעתך על ההסכם?')
assert(he.dominant_language === 'he' && he.text_direction === 'rtl', 'hebrew rtl')

const en = detectDominantLanguage('Launch Q2 in the US?')
assert(en.dominant_language === 'en' && en.text_direction === 'ltr', 'english ltr')

assert(detectDominantLanguage('') .dominant_language === 'en', 'empty fallback')

const heMsg = expertStatusLabel('he', 'timeout', '')
assert(heMsg.includes('פסק זמן'), 'hebrew timeout status')

const bubble = buildSynthesisBubbleText(
  { recommendation: 'x', consensus_points: 'y', agreement_estimate: '2/2' },
  true,
  'he'
)
assert(bubble.includes('קונצנזוס'), 'hebrew synthesis labels')

assert(councilPhaseTimers('he')[0].message.includes('המועצה'), 'hebrew council progress')

assert(!isMeaningfulReasoningValue('null'), 'null reasoning hidden')

assert(t('he', 'council_timeout').includes('פסק'), 'hebrew council timeout label')

console.log('PASS language-context')
