export const DEFAULT_STYLE = Object.freeze({ font: 'Arial', size: 5, color: '#ffffff', background: '#000000', opacity: 70, position: 'bottom' })
export const FONTS = ['Arial', 'Tahoma', 'Verdana']
export function validateCues(cues, duration) {
  if (!Array.isArray(cues) || cues.length > 300) throw new Error('Use up to 300 subtitles.')
  for (const cue of cues) {
    if (!Number.isFinite(cue.start) || !Number.isFinite(cue.end) || cue.start < 0 || cue.end <= cue.start || cue.end > duration + 0.05)
      throw new Error('Subtitle times must fit inside the video.')
    if (typeof cue.text !== 'string' || !cue.text.trim() || cue.text.length > 1000) throw new Error('Each subtitle needs 1–1,000 characters.')
  }
  return cues
}
export function parseSrt(value, duration) {
  if (value.length > 100000) throw new Error('Use an SRT file smaller than 100 KB.')
  const blocks = value.replace(/^\uFEFF/, '').replace(/\r/g, '').trim().split(/\n\s*\n/)
  const cues = blocks.map((block, id) => {
    const lines = block.split('\n')
    if (/^\d+$/.test(lines[0])) lines.shift()
    const m = /^(\d{2}):([0-5]\d):([0-5]\d)[,.](\d{3})\s+-->\s+(\d{2}):([0-5]\d):([0-5]\d)[,.](\d{3})$/.exec(lines.shift()?.trim())
    if (!m) throw new Error('This file has invalid SRT timestamps.')
    const seconds = i => Number(m[i]) * 3600 + Number(m[i + 1]) * 60 + Number(m[i + 2]) + Number(m[i + 3]) / 1000
    return { id: String(id), start: seconds(1), end: seconds(5), text: lines.join('\n') }
  })
  return validateCues(cues, duration)
}
export function activeSubtitle(cues, time) {
  return cues.filter(c => time >= c.start && time < c.end).map(c => c.text).join('\n')
}
function timestamp(seconds) {
  const ms = Math.round(seconds * 1000)
  return `${String(Math.floor(ms / 3600000)).padStart(2,'0')}:${String(Math.floor(ms / 60000) % 60).padStart(2,'0')}:${String(Math.floor(ms / 1000) % 60).padStart(2,'0')},${String(ms % 1000).padStart(3,'0')}`
}
export function toSrt(cues, duration) {
  return validateCues(cues, duration).slice().sort((a,b) => a.start - b.start).map((c,i) => `${i + 1}\n${timestamp(c.start)} --> ${timestamp(c.end)}\n${c.text}`).join('\n\n') + '\n'
}
