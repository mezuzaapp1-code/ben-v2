// Portable document data only: no storage, network, authorization or paid calls.
export const MAX_DOCUMENT_BYTES = 1_000_000
export const EDIT_STYLE_DEFAULTS = Object.freeze({
  font: 'Arial', size: 5, color: '#ffffff', background: '#000000', opacity: 70,
  position: 'bottom', alignment: 'auto', backgroundMode: 'solid', shadow: 'none',
  outlineColor: '#000000', outlineWidth: 0, spacing: 0,
  bold: false, italic: false, underline: false, x: 50, y: 80,
})
const fail = message => { throw new Error(message) }
function record(value, allowed, required = allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) fail('Expected plain object')
  if (Reflect.ownKeys(value).some(key => !allowed.includes(key))) fail('Unknown document field')
  if (required.some(key => !Object.hasOwn(value, key))) fail('Missing document field')
}
function jsonValue(value, seen = new Set(), depth = 0) {
  if (depth > 16) fail('Document nesting exceeds limit')
  if (value === null || typeof value === 'boolean') return
  if (typeof value === 'string') {
    // A lone surrogate is not a Unicode scalar; TextEncoder would silently replace it.
    for (const point of value) { const n = point.codePointAt(0); if (n >= 0xd800 && n <= 0xdfff) fail('Invalid Unicode') }
    return
  }
  if (typeof value === 'number') { if (!Number.isFinite(value)) fail('Expected finite number'); return }
  if (!value || typeof value !== 'object' || seen.has(value)) fail('Expected acyclic JSON data')
  if (!Array.isArray(value) && ![Object.prototype, null].includes(Object.getPrototypeOf(value))) fail('Expected JSON data')
  seen.add(value)
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) { if (!Object.hasOwn(value, i)) fail('Sparse array'); jsonValue(value[i], seen, depth + 1) }
  } else for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== 'string') fail('Expected string key')
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    if (!Object.hasOwn(descriptor, 'value') || !descriptor.enumerable) fail('Expected JSON properties')
    jsonValue(key, seen, depth + 1); jsonValue(descriptor.value, seen, depth + 1)
  }
  seen.delete(value)
}
function number(value, min, max) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) fail('Number out of bounds')
  return value
}
function uuid(value) {
  if (typeof value !== 'string' || value.length !== 36 || !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(value)) fail('Expected canonical UUID')
  return value.toLowerCase()
}
function style(value) {
  record(value, Object.keys(EDIT_STYLE_DEFAULTS), [])
  const s = { ...EDIT_STYLE_DEFAULTS, ...value }
  const enums = { font: ['Arial','Segoe UI','Tahoma','Verdana','Georgia','Times New Roman','Courier New'], position: ['bottom','center','top','custom'], alignment: ['auto','left','center','right'], backgroundMode: ['solid','none'], shadow: ['none','soft','depth','glow'] }
  for (const [key, values] of Object.entries(enums)) if (!values.includes(s[key])) fail('Unsupported style')
  for (const [key, [min,max]] of Object.entries({size:[2,12],opacity:[0,100],x:[0,100],y:[0,100],outlineWidth:[0,.08],spacing:[-.05,.3]})) number(s[key],min,max)
  for (const key of ['color','background','outlineColor']) if (typeof s[key] !== 'string' || s[key].length !== 7 || !/^#[\da-f]{6}$/i.test(s[key])) fail('Invalid color')
  for (const key of ['bold','italic','underline']) if (typeof s[key] !== 'boolean') fail('Expected boolean style')
  return s
}
function entries(items, duration, layers) {
  if (!Array.isArray(items) || items.length > (layers ? 20 : 300)) fail('Too many text items')
  const ids = new Set()
  return items.map(item => {
    record(item, layers ? ['id','text','start','end','style'] : ['id','text','start','end'])
    if (typeof item.id !== 'string' || item.id.length < 1 || item.id.length > 128 || /[^A-Za-z0-9_-]/.test(item.id) || ids.has(item.id)) fail('Invalid or duplicate text ID')
    ids.add(item.id)
    if (typeof item.text !== 'string' || [...item.text].length > 1000) fail('Invalid text')
    // Shared whitespace policy with Python, including NEL and separator controls.
    const blank = [...item.text].every(point => /[\s\u0085]/u.test(point) || (point.codePointAt(0) >= 28 && point.codePointAt(0) <= 31))
    if (!layers && blank) fail('Invalid text')
    const start = number(item.start, 0, duration + .05), end = number(item.end, 0, duration + .05)
    if (end <= start) fail('Text end must follow start')
    return {id:item.id,text:item.text,start,end,...(layers ? {style:style(item.style)} : {})}
  })
}
export function validateEditDocument(value) {
  jsonValue(value)
  if (new TextEncoder().encode(JSON.stringify(value)).length > MAX_DOCUMENT_BYTES) fail('Document is too large')
  record(value, ['schema_version','document_id','kind','source','body'])
  if (value.schema_version !== 'video-edit-v1' || value.kind !== 'video') fail('Unsupported edit document')
  record(value.source, ['resource_id','duration_seconds','timebase'], ['resource_id','duration_seconds'])
  if ((value.source.timebase ?? 'seconds') !== 'seconds' || value.source.timebase === null) fail('Unsupported timebase')
  const duration = number(value.source.duration_seconds, Number.MIN_VALUE, 86400)
  record(value.body, ['cues','style','textLayers'], ['cues','style'])
  const document = {
    schema_version:'video-edit-v1', document_id:uuid(value.document_id), kind:'video',
    source:{resource_id:uuid(value.source.resource_id),duration_seconds:duration,timebase:'seconds'},
    body:{cues:entries(value.body.cues,duration,false),style:style(value.body.style),textLayers:entries(Object.hasOwn(value.body,'textLayers') ? value.body.textLayers : [],duration,true)},
  }
  if (new TextEncoder().encode(JSON.stringify(document)).length > MAX_DOCUMENT_BYTES) fail('Normalized document is too large')
  return document
}
export function editorDraftToDocument(draft, {documentId, resourceId, durationSeconds}) {
  // Validate original input before spreading; no unrecognized or undefined fields disappear.
  jsonValue(draft)
  record(draft, ['cues','style','textLayers'], ['cues','style'])
  return validateEditDocument({schema_version:'video-edit-v1',document_id:documentId,kind:'video',
    source:{resource_id:resourceId,duration_seconds:durationSeconds,timebase:'seconds'},body:draft})
}
export function documentToEditorDraft(value, {resourceId, durationSeconds}) {
  const doc = validateEditDocument(value)
  // Selection/authorization still belongs to the caller/server; these checks prevent accidental reuse.
  if (doc.source.resource_id !== uuid(resourceId)) fail('Document belongs to a different video')
  number(durationSeconds, Number.MIN_VALUE, 86400)
  if (Math.abs(doc.source.duration_seconds - durationSeconds) > .05) fail('Source duration changed')
  // A shorter new source must also contain every restored item.
  return {cues:entries(doc.body.cues,durationSeconds,false),style:{...doc.body.style},textLayers:entries(doc.body.textLayers,durationSeconds,true)}
}
