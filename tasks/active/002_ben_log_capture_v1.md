# BEN Log Capture v1

## Goal

Append reasoning events on real AI-assisted work paths so continuity can be reconstructed from data — not from memory or scattered tabs.

## Why Now

P2 immediately after schema (P1). Without capture, threads remain conversation-only and cannot answer *where we stopped* or *what was rejected*.

## Scope

- `services/ben_log_service.py` (or `continuity_log_service.py`) — insert-only writes, `set_config` org RLS.
- Capture hooks after successful **chat** message persist (summary line + provider/model + thread_id).
- Capture hooks after **council** transcript persist (synthesis outcome, expert availability — no auto-governance).
- Optional: `POST /api/log/events` for human append (`decision_made`, `assumption_rejected`, `next_step`) — org JWT only, same auth pattern as future ledger.
- Structured English logs; no prompt full-text requirement in v1 (hash or truncated summary acceptable).

## Explicit NON-Goals

- Continuity read API (P3)
- Convergence summaries (P4)
- Ledger L2
- Council/chat behavior changes
- Auto-tagging via LLM
- Cross-thread intelligence
- Notifications, dashboard, analytics

## Dependencies

- **Blocked until** `001_ben_log_event_schema_v1.md` complete.
- Thread persist paths stable (`thread_service`, `chat_service`).

## Risks

- Capture failures block chat/council → must be non-blocking (log warning, do not fail 200).
- PII in payloads → keep summaries short; no raw secrets.
- Volume unbounded → defer retention policy; document in `research/` if needed.

## Verification

- Unit tests: event inserted with correct org_id, thread_id, source.
- Integration: one chat + one council run creates ≥1 row per thread in test DB.
- Regression: `test_chat_provider_routing`, `test_council_*` pass.
- Prod smoke: chat/council 200; new rows visible via psql for test org.

## Completion Criteria

- [ ] Events written on chat + council happy path
- [ ] Human append route (if in scope) returns 201 with JWT org tenant
- [ ] Failures in capture do not break user responses
- [ ] Tests in `tests/test_ben_log_capture_v1.py`
- [ ] Commit hash in `completed/`

## Next Task

`003_continuity_engine_v1.md` (create in `queued/` when P1–P2 done) — read aggregate per thread_id.
