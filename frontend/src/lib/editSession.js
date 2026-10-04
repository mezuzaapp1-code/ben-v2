import { editorDraftToDocument, documentToEditorDraft, validateEditDocument } from './editDocument.js'

// A save is a durable intent, not a generation request. Preserve its exact body
// and key before POST so a lost response/reload cannot silently create a new save.
export class EditSession {
  constructor({ resourceId, scope, request, storage, documentId, uuid = () => crypto.randomUUID() }) {
    this.resourceId = resourceId; this.request = request; this.storage = storage; this.uuid = uuid
    this.documentId = documentId; this.selectedId = documentId; this.head = null; this.pending = null; this.busy = false
    if (!scope || !resourceId) throw new Error('A signed-in account and video are required.')
    this.key = `ben:edit-pending:v1:${scope}:${resourceId}`
  }
  draft(document) { return documentToEditorDraft(document, { resourceId: this.resourceId, durationSeconds: this.duration }) }
  setRequest(request) { this.request = request }
  async open(duration) {
    if (!Number.isFinite(duration) || duration <= 0 || duration > 30)
      throw Object.assign(new Error('Saved editing currently supports videos up to 30 seconds.'), { code: 'EDIT_DURATION' })
    this.duration = duration
    // Authorization/discovery is always server-side, never a local document index.
    const { documents } = await this.request(`/edit-documents?resource_id=${encodeURIComponent(this.resourceId)}`)
    const raw = this.storage.getItem(this.key)
    if (raw) {
      if (raw.length > 1_100_000) throw new Error('The pending save could not be opened. It has been preserved.')
      const pending = JSON.parse(raw)
      validateEditDocument(pending.document)
      if (typeof pending.key !== 'string' || !/^[\w-]{1,128}$/.test(pending.key) ||
          !(pending.base === null || (typeof pending.base === 'string' && /^[\da-f-]{36}$/i.test(pending.base))))
        throw new Error('The pending save could not be opened. It has been preserved.')
      this.pending = pending; this.documentId = pending.document.document_id
      if (pending.base) this.head = await this.request(`/edit-documents/${this.documentId}`)
      return { draft: this.draft(pending.document), pending: true, revision: null }
    }
    const existing = this.selectedId || documents[0]?.document_id
    if (existing) { this.documentId = existing; return this.loadLatest() }
    this.documentId ||= this.uuid()
    return { draft: null, revision: null }
  }
  async loadLatest() {
    if (this.pending) throw new Error('Resolve the unconfirmed save before loading another version.')
    const head = await this.request(`/edit-documents/${this.documentId}`)
    const draft = this.draft(head.document)
    this.head = head
    return { draft, revision: head.revision_number }
  }
  async save(draft) {
    if (this.busy) throw new Error('A save is already in progress.')
    this.busy = true
    try {
      if (!this.pending) {
        const document = editorDraftToDocument(draft, { documentId: this.documentId,
          resourceId: this.resourceId, durationSeconds: this.duration })
        this.pending = { key: this.uuid(), base: this.head?.revision_id || null, document }
        try { this.storage.setItem(this.key, JSON.stringify(this.pending)) }
        catch { this.pending = null; throw new Error('Browser recovery storage is unavailable. No save was submitted; your edits remain open.') }
      }
      const pending = this.pending
      let result
      try {
        result = await this.request(pending.base ? `/edit-documents/${this.documentId}/revisions` : '/edit-documents', {
          body: pending.base ? { base_revision_id: pending.base, document: pending.document } : pending.document,
          key: pending.key,
        })
      } catch (error) {
        // These responses guarantee rejection, rather than an uncertain commit.
        if ([400,401,403,404,409,413,415,422,429].includes(error.status)) {
          this.storage.removeItem(this.key); this.pending = null
        }
        throw error
      }
      const savedDraft = this.draft(result.document)
      this.head = result
      // If cleanup fails, retrying the same retained key is safe.
      this.storage.removeItem(this.key); this.pending = null
      return { draft: savedDraft, revision: result.revision_number }
    } finally { this.busy = false }
  }
  history(before) {
    if (!this.head) return Promise.resolve({ revisions: [], next_before: null })
    return this.request(`/edit-documents/${this.documentId}/revisions${before ? `?before=${before}` : ''}`)
  }
  async revision(id) {
    const result = await this.request(`/edit-documents/${this.documentId}/revisions/${encodeURIComponent(id)}`)
    return this.draft(result.document)
  }
}

export function editFailure(error) {
  if (error.status === 409) return 'A newer version or conflicting save exists. Your changes are intact. Load the latest version; Undo keeps your changes available.'
  if ([401,403,404].includes(error.status)) return 'Saved editing is unavailable for this account or video. Your open changes have been kept.'
  if ([413,415,422,429].includes(error.status)) return 'This edit could not be saved. Check the video length, text timing and size, then try again.'
  return 'Save was not confirmed. Your changes are kept. Retry save to recover the same request.'
}
