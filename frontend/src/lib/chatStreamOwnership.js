/**
 * Bind /chat/stream provider output to the optimistic assistant for this send.
 * Action Cards must never become the chunk/done target.
 */

import { responseEvidenceFromDoneEvent } from './fileStatus.js'

export function isActionCard(message) {
  return message?.kind === 'action_card'
}

export function isOwnedStreamAssistant(message, sendNonce) {
  return Boolean(
    sendNonce &&
      message?.role === 'assistant' &&
      message?._sendNonce === sendNonce &&
      !isActionCard(message)
  )
}

export function createOwnedAssistant({ sendNonce, clientRequestId, providerId } = {}) {
  return {
    role: 'assistant',
    content: '',
    model_used: '',
    provider_id: providerId || '',
    provider_used: '',
    cost_usd: 0,
    _sendNonce: sendNonce,
    client_request_id: clientRequestId || undefined,
  }
}

export function appendActionCard(messages, event, { sendNonce } = {}) {
  return [
    ...(messages || []),
    {
      role: 'assistant',
      kind: 'action_card',
      card_type: event.card_type,
      action_payload: event.payload,
      content: '',
      model_used: '',
      cost_usd: 0,
      _sendNonce: sendNonce,
    },
  ]
}

export function applyOwnedAssistantChunk(messages, sendNonce, chunk) {
  const text = chunk ?? ''
  return (messages || []).map((message) => {
    if (!isOwnedStreamAssistant(message, sendNonce)) return message
    return { ...message, content: `${message.content || ''}${text}` }
  })
}

export function applyOwnedAssistantDone(messages, sendNonce, event, { speakingProviderId } = {}) {
  return (messages || []).map((message) => {
    if (message?.role === 'user' && message._sendNonce === sendNonce && event?.sqlite_user_id != null) {
      return { ...message, sqlite_message_id: event.sqlite_user_id }
    }
    if (!isOwnedStreamAssistant(message, sendNonce)) return message
    return {
      ...message,
      content: event?.response ?? message.content ?? '',
      model_used: event?.model_used ?? '',
      provider_id: event?.provider_id ?? speakingProviderId ?? message.provider_id,
      provider_used: event?.provider_used ?? '',
      cost_usd: event?.cost_usd ?? 0,
      ttft_ms: event?.ttft_ms ?? null,
      tps: event?.tps ?? null,
      sqlite_message_id: event?.sqlite_assistant_id ?? message.sqlite_message_id ?? null,
      used_files: event?.used_files,
      workspace_files_unavailable_note: event?.workspace_files_unavailable_note,
      response_evidence: responseEvidenceFromDoneEvent(event),
    }
  })
}

export function rollbackOwnedSend(messages, sendNonce) {
  return (messages || []).filter((message) => message?._sendNonce !== sendNonce)
}

export function ownedAssistant(messages, sendNonce) {
  return (messages || []).find((message) => isOwnedStreamAssistant(message, sendNonce)) || null
}

export function actionCards(messages, sendNonce) {
  return (messages || []).filter(
    (message) => isActionCard(message) && (!sendNonce || message._sendNonce === sendNonce)
  )
}
