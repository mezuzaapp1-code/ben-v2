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

// Bound auth / conversation creation as well as fetch. Late completions check the
// aborted signal before submitting a paid request; retries always keep identity.
export async function withMediaDeadline(action, parentSignal, milliseconds = 90000) {
  const controller = new AbortController()
  const abort = () => controller.abort()
  parentSignal?.addEventListener('abort', abort, { once: true })
  if (parentSignal?.aborted) controller.abort()
  const timer = setTimeout(abort, milliseconds)
  let listener
  try {
    return await Promise.race([
      Promise.resolve().then(() => { controller.signal.throwIfAborted(); return action(controller.signal) }),
      new Promise((_, reject) => {
        listener = () => reject(new Error('This is taking too long. Nothing was confirmed. Send again to safely recover your request.'))
        controller.signal.addEventListener('abort', listener, { once: true })
        if (controller.signal.aborted) listener()
      }),
    ])
  } finally {
    clearTimeout(timer)
    controller.signal.removeEventListener('abort', listener)
    parentSignal?.removeEventListener('abort', abort)
  }
}
