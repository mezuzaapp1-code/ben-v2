/** Authenticated workspace-file preview for Sources. No storage_key. */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function isWorkspaceFileId(value) {
  return UUID_RE.test(String(value || '').trim())
}

export function previewIframeSrc(blobUrl, { kind, page } = {}) {
  const base = String(blobUrl || '')
  if (!base) return ''
  const n = Number(page)
  if (kind === 'pdf' && Number.isInteger(n) && n >= 1) {
    return `${base}#page=${n}`
  }
  return base
}

export function revokePreviewUrl(blobUrl) {
  if (!blobUrl) return
  try {
    URL.revokeObjectURL(blobUrl)
  } catch {
    /* already revoked */
  }
}

export async function openWorkspaceFilePreview({
  workspaceId,
  fileId,
  page,
  headers,
  allowedSourceIds,
} = {}) {
  const id = String(fileId || '').trim()
  if (!workspaceId || !isWorkspaceFileId(id)) {
    throw new Error('File is not available.')
  }
  const allowed = allowedSourceIds instanceof Set ? allowedSourceIds : new Set(allowedSourceIds || [])
  if (!allowed.has(id) && !allowed.has(id.toLowerCase())) {
    throw new Error('File is not a source for this response.')
  }
  const { fetchWorkspaceFileBlob, getWorkspaceFile } = await import('../api/workspaceFiles.js')
  const meta = await getWorkspaceFile(workspaceId, id, headers, { includeTextPreview: true })
  const kind = meta?.preview_kind || 'download'
  if (kind === 'image' || kind === 'pdf') {
    const blob = await fetchWorkspaceFileBlob(workspaceId, id, headers, { inline: true })
    const blobUrl = URL.createObjectURL(blob)
    return {
      kind,
      displayName: meta.display_name || 'file',
      blobUrl,
      src: previewIframeSrc(blobUrl, { kind, page }),
      text: '',
    }
  }
  return {
    kind: kind === 'text' ? 'text' : 'download',
    displayName: meta.display_name || 'file',
    blobUrl: null,
    src: '',
    text: meta.text_preview || '',
  }
}
