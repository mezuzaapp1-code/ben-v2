"""V1 conversation-source lifetime policy.

Tune these defaults here only. Callers must not hardcode unused-turn,
idle-TTL, or burst-window numbers.
"""
from __future__ import annotations

# Consecutive standard-chat turns that inject none of the active files
# before active → recent. One unused turn must not exit (upload → chit-chat
# → "summarize the proposal").
ACTIVE_SOURCE_MAX_UNUSED_TURNS = 2

# Idle after last injection (or after active opened if never used).
ACTIVE_SOURCE_IDLE_TTL_MINUTES = 20

# Sequential chat-originated uploads within this window join one pending cohort.
UPLOAD_BURST_WINDOW_SECONDS = 120

FILE_INITIAL_READ_EVENT = "file_initial_read"

INITIAL_READ_NONE = "none"
INITIAL_READ_PENDING = "pending"
INITIAL_READ_COMPLETE = "complete"
INITIAL_READ_FAILED = "failed"
INITIAL_READ_SKIPPED = "skipped"
INITIAL_READ_STATUSES = (
    INITIAL_READ_NONE,
    INITIAL_READ_PENDING,
    INITIAL_READ_COMPLETE,
    INITIAL_READ_FAILED,
    INITIAL_READ_SKIPPED,
)
