# DeepSeek in BEN

DeepSeek is a speaking provider in the existing chat selector. Its default is
`deepseek-flash`; `deepseek-v4-pro` is also selectable. Both dispatch to their
exact API IDs at `https://api.deepseek.com/chat/completions`. An explicit selection
has one gateway attempt and never falls back to another provider. DeepSeek is
not added to the implicit tier fallback chain.

Set `DEEPSEEK_API_KEY` in the backend environment/secret store. Never configure it
in frontend variables or commit a value. Missing credentials, HTTP errors and
timeouts follow BEN's existing error contract.

The adapter subclasses the existing Chat Completions transport. OpenAI's URL,
key lookup, payload (including Astra reasoning effort), stream handling and
usage normalization remain the defaults. DeepSeek overrides only provider policy:
endpoint/key, non-thinking chat payload, usage normalization and strict response
validation. It ignores reasoning deltas, rejects malformed/incomplete streams,
and surfaces provider errors without copying remote error text.

BEN still prepares history, workspace/file context and current-turn attachments,
owns conversations and memory, persists exchanges, and emits the existing NDJSON
`chunk` / `error` / `done` events. There is no DeepSeek context store or tool loop.
Clean Chat does not acquire Project file context.

## Capabilities

- Both registered models support ordinary text chat and SSE streaming.
- BEN sends `thinking: {"type": "disabled"}` for ordinary chat, with no tools,
  search, temperature or reasoning-effort fields.
- Flash supports image input through BEN's existing `vision.analyze` path.
- Pro has no vision capability; the existing capability gate rejects images,
  and the adapter also rejects direct image requests to Pro.
- Tool calling, JSON mode and thinking are not exposed by this integration.

## Usage and cost

DeepSeek's cache-hit field is normalized to BEN cached input tokens. Its
completion count includes reasoning, so reasoning is split out before BEN's
existing cost calculator adds output and reasoning costs. Missing usage remains
unknown rather than fabricated.

The registry records the published peak/off-peak rates. Peak windows are weekdays
01:00–04:00 and 06:00–10:00 UTC; other times use off-peak rates. BEN selects the
rate when accounting the completed request. These are local estimates, not a
provider invoice; requests spanning a price-window boundary may differ from
provider billing. Existing providers' rates and reasoning settings are unchanged.

## Verification

Run `python -m pytest tests/test_deepseek_provider.py tests/test_deepseek_chat.py`
and `npm --prefix frontend run test:deepseek-provider-menu`. Run the existing
provider, Astra, vision, inference-accounting, workspace and Clean Chat regression
tests as well. All provider calls in these tests are deterministic mocks.

For a live smoke test, set the backend environment variable and run
`python scripts/deepseek_smoke.py`. It prints only model IDs, character counts and
usage status, never credentials or response bodies. This does not deploy BEN.

## Provider references

Verified against official documentation on 2026-09-20:

- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [Models and pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)

Use current model IDs instead of assuming older `deepseek-chat` or
`deepseek-reasoner` aliases are the current product catalog.
