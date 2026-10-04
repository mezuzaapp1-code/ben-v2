import { pendingMedia, getPendingMedia, clearPendingMedia, mediaRequest } from './media.js'
import { uploadPhoto } from './photoUpload.js'

export async function submitMedia({ scope, ensureConversation, buildHeaders, intent, file, signal, onStage }) {
  onStage('Preparing your conversation…')
  const id = await ensureConversation()
  signal.throwIfAborted()
  const key = `${scope}:${id}`
  let body = getPendingMedia(sessionStorage, key)
  if (!body) {
    let source
    if (file) {
      onStage('Uploading your photo…')
      source = await uploadPhoto({ file, conversationId: id, scope, buildHeaders, signal })
    }
    signal.throwIfAborted()
    body = pendingMedia(sessionStorage, key, { ...intent, conversation_id: id,
      ...(source ? { source_file_id: source.file_id, workspace_id: source.workspace_id, source_resource_id: undefined } : {}) })
  }
  onStage('Sending your request…')
  const headers = await buildHeaders()
  signal.throwIfAborted()
  try {
    const result = await mediaRequest('/executions', headers, { body, signal })
    signal.throwIfAborted()
    clearPendingMedia(sessionStorage, key)
    return result
  } catch (error) {
    if ([400, 401, 403, 404, 409, 413, 422, 429].includes(error.status)) {
      clearPendingMedia(sessionStorage, key)
      throw new Error('The request was rejected. Check the image and video settings, then try again.', { cause: error })
    }
    throw new Error('Submission was not confirmed. Send again to recover the same request; BEN will not create a duplicate.', { cause: error })
  }
}

export { withMediaDeadline } from './mediaDeadline.js'
