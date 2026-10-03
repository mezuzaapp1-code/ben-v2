import { DEFAULT_STYLE, FONTS, validateCues } from './subtitleDraft.js'

const PREFIX = 'ben:video-editor:v1:'
const MAX_LENGTH = 1000000
function checkedStyle(value) {
  if (!value || typeof value !== 'object') throw new Error('Invalid saved style.')
  const result = { ...DEFAULT_STYLE }
  const enums = { font: FONTS, position: ['bottom','top','center','custom'], alignment: ['auto','left','center','right'], backgroundMode: ['none','solid'], shadow: ['none','soft','depth','glow'] }
  const ranges = { size: [2,12], opacity: [0,100], x: [0,100], y: [0,100], outlineWidth: [0,.08], spacing: [-.05,.3] }
  for (const [key, options] of Object.entries(enums)) if (Object.hasOwn(value,key)) {
    if (!options.includes(value[key])) throw new Error('Invalid saved style.')
    result[key] = value[key]
  }
  for (const [key,[min,max]] of Object.entries(ranges)) if (Object.hasOwn(value,key)) {
    if (!Number.isFinite(value[key]) || value[key] < min || value[key] > max) throw new Error('Invalid saved style.')
    result[key] = value[key]
  }
  for (const key of ['color','background','outlineColor']) if (Object.hasOwn(value,key)) {
    if (!/^#[\da-f]{6}$/i.test(value[key])) throw new Error('Invalid saved color.')
    result[key] = value[key]
  }
  for (const key of ['bold','italic','underline']) if (Object.hasOwn(value,key)) {
    if (typeof value[key] !== 'boolean') throw new Error('Invalid saved style.')
    result[key] = value[key]
  }
  return result
}
export function checkedDraft(value, duration) {
  if (!Number.isFinite(duration) || duration <= 0 || !value) throw new Error('Wait for the video to load before saving.')
  validateCues(value.cues, duration)
  const layers = value.textLayers || []
  if (!Array.isArray(layers) || layers.length > 20) throw new Error('Use up to 20 text layers.')
  const entries = (items, styled) => {
    const ids = new Set()
    return items.map(item => {
      if (!item || typeof item.id !== 'string' || !item.id || item.id.length > 128 || ids.has(item.id)) throw new Error('Invalid saved text layer.')
      ids.add(item.id)
      if (typeof item.text !== 'string' || item.text.length > 1000) throw new Error('Invalid saved text.')
      validateCues([{ ...item, text: item.text.trim() ? item.text : 'Draft text' }], duration)
      return { id: item.id, text: item.text, start: item.start, end: item.end, ...(styled ? { style: checkedStyle(item.style) } : {}) }
    })
  }
  return { cues: entries(value.cues,false), style: checkedStyle(value.style), textLayers: entries(layers,true) }
}
export function readEditorDraft(key) {
  if (!key) return { saved: null, error: '' }
  try {
    const raw = window.localStorage.getItem(PREFIX + key)
    if (!raw) return { saved: null, error: '' }
    if (raw.length > MAX_LENGTH) throw new Error('Oversized draft.')
    const value = JSON.parse(raw)
    if (value.version !== 1) throw new Error('Unknown draft version.')
    return { saved: { draft: checkedDraft(value.draft,value.duration), duration: value.duration }, error: '' }
  } catch { return { saved: null, error: 'The saved draft could not be opened. It has not been overwritten.' } }
}
export function writeEditorDraft(key, draft, duration) {
  if (!key) throw new Error('Local saving is unavailable for this video.')
  const payload = JSON.stringify({ version: 1, duration, draft: checkedDraft(draft,duration) })
  if (payload.length > MAX_LENGTH) throw new Error('This draft is too large to save locally.')
  try { window.localStorage.setItem(PREFIX + key,payload) }
  catch { throw new Error('Save failed: browser storage is full or unavailable. Your edits are still open.') }
}
