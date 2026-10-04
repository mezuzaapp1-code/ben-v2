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
