/**
 * Stream ownership: provider output stays on the optimistic assistant.
 * Run: node frontend/scripts/test-chat-stream-ownership.mjs
 */
import {
  actionCards,
  appendActionCard,
  applyOwnedAssistantChunk,
  applyOwnedAssistantDone,
  createOwnedAssistant,
  isOwnedStreamAssistant,
  ownedAssistant,
  rollbackOwnedSend,
} from '../src/lib/chatStreamOwnership.js'

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

function play(events, { sendNonce = 'send-1', speakingProviderId = 'gpt' } = {}) {
  const user = { role: 'user', content: 'hi', _sendNonce: sendNonce }
  const assistant = createOwnedAssistant({ sendNonce, clientRequestId: 'req-1', providerId: speakingProviderId })
  let messages = [user, assistant]
  for (const event of events) {
    if (event.type === 'mutated_state') {
      messages = appendActionCard(messages, event, { sendNonce })
    } else if (event.type === 'chunk') {
      messages = applyOwnedAssistantChunk(messages, sendNonce, event.content)
    } else if (event.type === 'done') {
      messages = applyOwnedAssistantDone(messages, sendNonce, event, { speakingProviderId })
    }
  }
  return messages
}

{
  const nonce = 'send-cards'
  const messages = play(
    [
      { type: 'mutated_state', card_type: 'lifecycle_overview', payload: { message: 'Travel from Or Akiva' } },
      { type: 'mutated_state', card_type: 'government_intelligence', payload: { site_address: 'verify BEN' } },
      { type: 'chunk', content: 'BEN' },
      { type: 'chunk', content: '-LP-END-92741' },
      {
        type: 'done',
        response: 'BEN-LP-END-92741',
        model_used: 'gpt-test',
        provider_id: 'gpt',
        cost_usd: 0.0123,
        sqlite_user_id: 11,
        sqlite_assistant_id: 12,
      },
    ],
    { sendNonce: nonce }
  )

  assert(messages.length === 4, 'user + assistant + 2 cards')
  const asst = ownedAssistant(messages, nonce)
  assert(asst, 'owned assistant still present')
  assert(asst.content === 'BEN-LP-END-92741', `assistant shows provider text, got ${asst.content}`)
  assert(asst.model_used === 'gpt-test', 'model_used on assistant')
  assert(asst.cost_usd === 0.0123, 'cost on assistant')
  assert(asst.provider_id === 'gpt', 'provider metadata on assistant')
  assert(asst.kind !== 'action_card', 'assistant is not an action card')
  assert(messages[0].sqlite_message_id === 11, 'sqlite user id bound by send nonce, not position')

  const cards = actionCards(messages, nonce)
  assert(cards.length === 2, 'both action cards remain')
  assert(cards[0].card_type === 'lifecycle_overview', 'first card intact')
  assert(cards[1].card_type === 'government_intelligence', 'second card intact')
  assert(cards.every((card) => card.content === ''), 'ActionCard.content does not receive provider text')
  assert(cards[0].action_payload.message === 'Travel from Or Akiva', 'card payload unchanged')
  assert(messages[messages.length - 1].kind === 'action_card', 'last message is a card — ownership ignores position')
  assert(!isOwnedStreamAssistant(messages[messages.length - 1], nonce), 'last card is not the stream target')
}

{
  const nonce = 'send-zero'
  const messages = play(
    [
      { type: 'chunk', content: 'hello ' },
      { type: 'chunk', content: 'world' },
      { type: 'done', response: 'hello world', model_used: 'm', provider_id: 'gpt', cost_usd: 0.001 },
    ],
    { sendNonce: nonce }
  )
  assert(messages.length === 2, 'zero cards: user + assistant only')
  assert(ownedAssistant(messages, nonce).content === 'hello world', 'short chat chunks land on assistant')
  assert(actionCards(messages).length === 0, 'no cards invented')
}

{
  const nonce = 'send-short'
  const messages = play(
    [{ type: 'done', response: 'BEN-SHORT-92741', model_used: 'gpt-x', provider_id: 'gpt', cost_usd: 0 }],
    { sendNonce: nonce }
  )
  assert(ownedAssistant(messages, nonce).content === 'BEN-SHORT-92741', 'normal short chat done path')
}

{
  const nonce = 'send-rollback'
  let messages = play(
    [{ type: 'mutated_state', card_type: 'lifecycle_overview', payload: {} }],
    { sendNonce: nonce }
  )
  messages.push({ role: 'user', content: 'other', _sendNonce: 'other' })
  const rolled = rollbackOwnedSend(messages, nonce)
  assert(rolled.every((m) => m._sendNonce !== nonce), 'failed send removes owned user, assistant, and cards')
  assert(rolled.some((m) => m._sendNonce === 'other'), 'other turns stay')
}

{
  const a = createOwnedAssistant({ sendNonce: 'a', providerId: 'gpt' })
  const b = createOwnedAssistant({ sendNonce: 'b', providerId: 'claude' })
  let messages = [
    { role: 'user', content: 'a', _sendNonce: 'a' },
    a,
    { role: 'user', content: 'b', _sendNonce: 'b' },
    b,
  ]
  messages = appendActionCard(messages, { card_type: 'lifecycle_overview', payload: {} }, { sendNonce: 'a' })
  messages = applyOwnedAssistantChunk(messages, 'b', 'from-b')
  assert(ownedAssistant(messages, 'a').content === '', 'other send nonce is not mutated')
  assert(ownedAssistant(messages, 'b').content === 'from-b', 'chunks follow send nonce, not last index')
}

console.log('OK chat stream ownership')
