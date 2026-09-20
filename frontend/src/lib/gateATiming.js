/**
 * Opt-in Gate A browser timeline. Default OFF.
 *
 * Enable: localStorage.setItem('BEN_GATE_A_TIMING', '1')
 * Does not add request headers (no extra CORS preflight).
 * Does not change fetch options, buffering, or render path.
 */
const MARK_PREFIX = 'ben_gate_a:'

function enabled() {
  try {
    return globalThis.localStorage?.getItem('BEN_GATE_A_TIMING') === '1'
  } catch {
    return false
  }
}

export function gateAEnabled() {
  return enabled()
}

export function gateAMark(name, extra) {
  if (!enabled()) return
  try {
    performance.mark(`${MARK_PREFIX}${name}`)
  } catch {
    /* ignore */
  }
  if (extra && typeof extra === 'object') {
    // Bounded: names/status only, never payload/token text.
    const safe = {}
    for (const [k, v] of Object.entries(extra)) {
      if (k === 'message' || k === 'token' || k === 'authorization') continue
      if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') safe[k] = v
    }
    // eslint-disable-next-line no-console
    console.debug('ben_gate_a', name, safe)
  } else {
    // eslint-disable-next-line no-console
    console.debug('ben_gate_a', name)
  }
}

export function gateAMeasure(name, startMark, endMark) {
  if (!enabled()) return null
  try {
    const start = `${MARK_PREFIX}${startMark}`
    const end = `${MARK_PREFIX}${endMark}`
    performance.measure(`${MARK_PREFIX}${name}`, start, end)
    const entries = performance.getEntriesByName(`${MARK_PREFIX}${name}`)
    const last = entries[entries.length - 1]
    return last ? Math.round(last.duration * 10) / 10 : null
  } catch {
    return null
  }
}

/** Bounded interval dump for opt-in capture. Numbers only. */
export function gateALogSummary(extra) {
  if (!enabled()) return
  const spans = [
    ['F0_to_F1', 'F0_submit', 'F1_fetch'],
    ['F1_to_Fh', 'F1_fetch', 'Fh_headers'],
    ['Fh_to_Fr', 'Fh_headers', 'Fr_first_body'],
    ['Fr_to_F2', 'Fr_first_body', 'F2_first_answer'],
    ['F2_to_Fc', 'F2_first_answer', 'Fc_dom_commit_approx'],
    ['F0_to_F2', 'F0_submit', 'F2_first_answer'],
    ['F0_to_Fc', 'F0_submit', 'Fc_dom_commit_approx'],
    ['F0_to_F4', 'F0_submit', 'F4_stream_end'],
  ]
  const out = { first_text_event: 'F2_first_answer', note: 'F2 is FIRST_TEXT_EVENT; FIRST_ANSWER_CONTENT is server p4_answer' }
  for (const [name, start, end] of spans) {
    const ms = gateAMeasure(name, start, end)
    if (ms != null) out[name] = ms
  }
  if (extra && typeof extra === 'object') {
    for (const [k, v] of Object.entries(extra)) {
      if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') out[k] = v
    }
  }
  // eslint-disable-next-line no-console
  console.info('ben_gate_a_summary', out)
}
