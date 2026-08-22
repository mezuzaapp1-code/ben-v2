/**
 * Large Paste V1 — conversation-scoped composer parts.
 *
 * User action determines semantics (this is chat content).
 * Size determines representation: a single paste event at/above the
 * threshold becomes a structured large_paste part instead of inline text.
 *
 * Never a WorkspaceFile / File Library / Project Knowledge item.
 */

export const LARGE_PASTE_THRESHOLD = 10_000
export const LARGE_PASTE_UNWRAP_CEILING = 25_000
export const LARGE_PASTE_PROVIDER_MAX_CHARS = 400_000
export const USER_TURN_KIND = 'user_turn'
export const LARGE_PASTE_DEFAULT_LABEL = 'Pasted text'

const BEN_PREFIX = '{"ben":'

export function codePointCount(text) {
  return Array.from(text ?? '').length
}

export function formatCharCount(n) {
  return Number(n || 0).toLocaleString('en-US')
}

export function formatLargePasteStub(charCount) {
  return `[Large paste · ${formatCharCount(charCount)} characters]`
}

export function formatPasteChipLabel(part) {
  const label = String(part?.label || LARGE_PASTE_DEFAULT_LABEL)
  const count = part?.char_count ?? codePointCount(part?.text || '')
  return `${label} · ${formatCharCount(count)} characters`
}

export function emptyComposerParts() {
  return [{ type: 'text', text: '' }]
}

export function cloneParts(parts) {
  return (parts || []).map((part) => ({ ...part }))
}

function newPasteId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `paste-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function nextPasteLabel(parts) {
  const n = (parts || []).filter((part) => part?.type === 'large_paste').length
  if (n <= 0) return LARGE_PASTE_DEFAULT_LABEL
  return `${LARGE_PASTE_DEFAULT_LABEL} ${n + 1}`
}

export function shouldCreateLargePaste(text) {
  return codePointCount(text) >= LARGE_PASTE_THRESHOLD
}

export function normalizeComposerParts(parts) {
  const raw = Array.isArray(parts) ? parts : emptyComposerParts()
  const out = []
  for (const part of raw) {
    if (!part || typeof part !== 'object') continue
    if (part.type === 'text') {
      out.push({ type: 'text', text: String(part.text ?? '') })
      continue
    }
    if (part.type === 'large_paste') {
      const text = String(part.text ?? '')
      out.push({
        type: 'large_paste',
        id: String(part.id || newPasteId()),
        label: String(part.label || LARGE_PASTE_DEFAULT_LABEL),
        text,
        char_count: codePointCount(text),
      })
    }
  }
  if (out.length === 0) return emptyComposerParts()
  if (out[0].type !== 'text') out.unshift({ type: 'text', text: '' })
  if (out[out.length - 1].type !== 'text') out.push({ type: 'text', text: '' })
  return out
}

export function hasLargePaste(parts) {
  return (parts || []).some((part) => part?.type === 'large_paste')
}

export function canSendComposerParts(parts) {
  return (parts || []).some((part) => {
    if (part?.type === 'large_paste') return Boolean(part.text)
    if (part?.type === 'text') return Boolean(String(part.text || '').trim())
    return false
  })
}

export function instructionTextFromParts(parts) {
  return (parts || [])
    .filter((part) => part?.type === 'text')
    .map((part) => String(part.text || ''))
    .join('')
}

export function displayTextFromParts(parts) {
  return (parts || [])
    .map((part) => {
      if (part?.type === 'text') return String(part.text || '')
      if (part?.type === 'large_paste') {
        const count = part.char_count ?? codePointCount(part.text || '')
        return formatLargePasteStub(count)
      }
      return ''
    })
    .join('')
}

export function expandPartsForProvider(parts) {
  return (parts || []).map((part) => String(part?.text || '')).join('')
}

export function compactPartsForEncode(parts) {
  return (parts || []).filter((part) => {
    if (part?.type === 'large_paste') return Boolean(part.text)
    if (part?.type === 'text') return part.text !== ''
    return false
  })
}

export function encodeUserTurn(parts) {
  const compact = compactPartsForEncode(normalizeComposerParts(parts)).map((part) => {
    if (part.type === 'text') return { type: 'text', text: part.text }
    return {
      type: 'large_paste',
      id: part.id,
      label: part.label || LARGE_PASTE_DEFAULT_LABEL,
      text: part.text,
      char_count: codePointCount(part.text),
    }
  })
  if (compact.length === 0) return ''
  if (!compact.some((part) => part.type === 'large_paste')) {
    return compact.filter((part) => part.type === 'text').map((part) => part.text).join('')
  }
  return JSON.stringify({ ben: 1, kind: USER_TURN_KIND, parts: compact })
}

function sanitizeDecodedParts(raw) {
  if (!Array.isArray(raw) || raw.length === 0) return null
  const out = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') return null
    if (item.type === 'text') {
      if (typeof item.text !== 'string') return null
      out.push({ type: 'text', text: item.text })
      continue
    }
    if (item.type === 'large_paste') {
      if (typeof item.text !== 'string') return null
      const id = String(item.id || '').trim()
      if (!id) return null
      const text = item.text
      out.push({
        type: 'large_paste',
        id,
        label: String(item.label || LARGE_PASTE_DEFAULT_LABEL).trim() || LARGE_PASTE_DEFAULT_LABEL,
        text,
        char_count: codePointCount(text),
      })
      continue
    }
    return null
  }
  return out
}

export function parseUserTurnParts(content) {
  const raw = String(content ?? '')
  if (!raw.startsWith(BEN_PREFIX)) return null
  let data
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }
  if (!data || data.ben !== 1 || data.kind !== USER_TURN_KIND) return null
  return sanitizeDecodedParts(data.parts)
}

export function decodeUserTurnContent(content) {
  const parts = parseUserTurnParts(content)
  if (parts == null) {
    return { role: 'user', content: String(content ?? ''), parts: null }
  }
  return {
    role: 'user',
    kind: USER_TURN_KIND,
    content: displayTextFromParts(parts),
    parts,
  }
}

export function composerPartsFromMessage(message) {
  if (Array.isArray(message?.parts) && message.parts.length) {
    return normalizeComposerParts(message.parts)
  }
  const parsed = parseUserTurnParts(message?.content)
  if (parsed) return normalizeComposerParts(parsed)
  return [{ type: 'text', text: String(message?.content ?? '') }]
}

export function focusSourceFromParts(parts) {
  const instruction = instructionTextFromParts(parts).trim()
  if (instruction) return instruction
  const stubs = (parts || [])
    .filter((part) => part?.type === 'large_paste')
    .map((part) => formatLargePasteStub(part.char_count ?? codePointCount(part.text || '')))
  return stubs.join(' ')
}

export function insertLargePasteAtCursor(parts, textPartIndex, selectionStart, selectionEnd, pastedText) {
  const normalized = normalizeComposerParts(parts)
  const idx = Number(textPartIndex)
  const current = normalized[idx]
  if (!current || current.type !== 'text') return normalized
  const text = current.text
  const start = Math.max(0, Math.min(Number(selectionStart) || 0, text.length))
  const end = Math.max(start, Math.min(Number(selectionEnd) || start, text.length))
  const before = text.slice(0, start)
  const after = text.slice(end)
  const pastePart = {
    type: 'large_paste',
    id: newPasteId(),
    label: nextPasteLabel(normalized),
    text: String(pastedText ?? ''),
    char_count: codePointCount(pastedText),
  }
  const next = [
    ...normalized.slice(0, idx),
    { type: 'text', text: before },
    pastePart,
    { type: 'text', text: after },
    ...normalized.slice(idx + 1),
  ]
  return normalizeComposerParts(next)
}

export function unwrapLargePaste(parts, pasteIndex) {
  const normalized = normalizeComposerParts(parts)
  const idx = Number(pasteIndex)
  const current = normalized[idx]
  if (!current || current.type !== 'large_paste') {
    return { ok: false, reason: 'No Large Paste at that position.', parts: normalized }
  }
  const count = codePointCount(current.text)
  if (count > LARGE_PASTE_UNWRAP_CEILING) {
    return {
      ok: false,
      reason:
        `Too large to show in the text field (${formatCharCount(count)} characters; ` +
        `limit ${formatCharCount(LARGE_PASTE_UNWRAP_CEILING)}). The paste was not changed.`,
      parts: normalized,
    }
  }
  const next = [
    ...normalized.slice(0, idx),
    { type: 'text', text: current.text },
    ...normalized.slice(idx + 1),
  ]
  const merged = []
  for (const part of next) {
    if (part.type === 'text' && merged.length && merged[merged.length - 1].type === 'text') {
      merged[merged.length - 1] = {
        type: 'text',
        text: `${merged[merged.length - 1].text}${part.text}`,
      }
    } else {
      merged.push(part)
    }
  }
  return { ok: true, parts: normalizeComposerParts(merged), reason: '' }
}

export function providerExpansionError(expanded) {
  const n = codePointCount(expanded)
  if (n <= LARGE_PASTE_PROVIDER_MAX_CHARS) return null
  return (
    `This message is ${formatCharCount(n)} characters. BEN will not send more than ` +
    `${formatCharCount(LARGE_PASTE_PROVIDER_MAX_CHARS)} characters in one request. ` +
    'This is a BEN transport limit, not a guarantee that the selected model can fit the content. ' +
    'The Large Paste was not truncated and remains in the composer.'
  )
}
