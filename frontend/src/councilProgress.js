/** Council progressive UX: phases, long-prompt hint, RTL detection (no secrets). */

export const COUNCIL_TIMEOUT_USER_MESSAGE =
  'Council took longer than expected. Please retry or shorten the request.'

export const COUNCIL_LONG_PROMPT_HINT =
  'This is a complex request and may take longer.'

/** Longer prompts often exceed client council budget (35s). */
export const LONG_PROMPT_CHAR_THRESHOLD = 400
export const LONG_PROMPT_WORD_THRESHOLD = 80

export const COUNCIL_PHASE_LABELS = {
  started: 'Council started',
  legal: 'Waiting for Legal Advisor',
  business: 'Waiting for Business Advisor',
  strategy: 'Waiting for Strategy Advisor',
  synthesizing: 'Synthesizing',
}

export const COUNCIL_PHASE_ORDER = ['started', 'legal', 'business', 'strategy', 'synthesizing']

/** Staggered UI phases while waiting for single blocking /council response. */
export const COUNCIL_PHASE_SCHEDULE_MS = [
  { at: 0, phase: 'started' },
  { at: 50, phase: 'legal' },
  { at: 3_500, phase: 'business' },
  { at: 7_000, phase: 'strategy' },
  { at: 12_000, phase: 'synthesizing' },
]

const TECHNICAL_ERROR = /ReadTimeout|ECONNRESET|ETIMEDOUT|AbortError|fetch failed|network error|error:\s*ReadTimeout/i

export function isLongCouncilPrompt(text) {
  const t = String(text || '').trim()
  if (!t) return false
  if (t.length >= LONG_PROMPT_CHAR_THRESHOLD) return true
  const words = t.split(/\s+/).filter(Boolean)
  return words.length >= LONG_PROMPT_WORD_THRESHOLD
}

/** Hebrew and related scripts — use for RTL-friendly rendering, not as an error signal. */
export function isRtlPreferredText(text) {
  return /[\u0590-\u05FF\u0600-\u06FF]/.test(String(text || ''))
}

export function sanitizeCouncilErrorMessage(raw) {
  const s = String(raw ?? '').trim()
  if (!s) return COUNCIL_TIMEOUT_USER_MESSAGE
  if (TECHNICAL_ERROR.test(s)) return COUNCIL_TIMEOUT_USER_MESSAGE
  if (/^error:\s*/i.test(s) && s.length < 120) return COUNCIL_TIMEOUT_USER_MESSAGE
  return s
}
