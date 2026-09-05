import { isDraftThreadId, isPersistedThreadId } from '../threadStorage.js'

/**
 * Chat attach must send a real server thread UUID as source_chat_id.
 * Draft IDs are frontend-local; backend parse_thread_uuid rejects them.
 *
 * @param {string | null | undefined} localThreadId
 * @param {() => Promise<{ thread?: { id?: string }, id?: string }>} persistThread
 */
export async function ensurePersistedThreadForUpload(localThreadId, persistThread) {
  if (isPersistedThreadId(localThreadId)) {
    return { threadId: localThreadId, created: false, replacedDraftId: null }
  }
  if (!isDraftThreadId(localThreadId)) {
    throw new Error('Cannot attach a file without a conversation.')
  }
  const created = await persistThread()
  const threadId = created?.thread?.id || created?.id
  if (!isPersistedThreadId(threadId)) {
    throw new Error('Conversation could not be saved before upload.')
  }
  return { threadId, created: true, replacedDraftId: localThreadId }
}

/** Swap a local draft conversation for the persisted server thread. */
export function adoptPersistedThreadInList(threads, fromId, serverTid) {
  if (!Array.isArray(threads) || !serverTid || !fromId || fromId === serverTid) {
    return threads
  }
  const nextList = threads.map((t) => {
    if (t.id !== fromId && t.id !== serverTid) return t
    return { ...t, id: serverTid, isDraft: false }
  })
  if (!nextList.some((t) => t.id === serverTid)) {
    const src = threads.find((t) => t.id === fromId)
    if (src) return [{ ...src, id: serverTid, isDraft: false }, ...nextList]
  }
  return nextList
}
