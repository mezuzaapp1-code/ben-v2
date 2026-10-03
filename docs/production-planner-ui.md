# Production planner UI

This change adds **Plan a video** to the composer menu when the existing pilot capabilities expose `production_planning`. `BEN_MEDIA_PLAN_ENABLED` stays off by default. It depends on migration 038 and the production-plan-v1 branch.

The workspace preserves the complete user brief, accepts JPEG/PNG references, assigns their intended roles, and edits three five-second scenes. The initial outline is manual: no model interprets the brief, no audio is generated, and no rendering or paid provider endpoint is called. A 60-second brief is retained without pretending to produce a 60-second plan.

The scene panel opens/closes beside the storyboard on desktop and below it on narrow screens. The dialog traps keyboard focus, restores the opener, and asks before discarding edits. Reference images are not generated previews.

Saving creates immutable plan versions through the existing owner-scoped API. Before submission, the browser records an exact request and idempotency key in session storage scoped to owner and conversation. An ambiguous response locks editing until the exact request is retried. Reopening restores an unresolved submission or the latest saved version. Unsupported advanced plans remain read-only.

New read endpoints retrieve the latest owned conversation plan and protected photo previews. Photo responses use private/no-store and nosniff; existing source ownership and checksum checks run before returning bytes.

Validation includes the planner interaction test, API owner forwarding and protected response tests, existing PostgreSQL owner/version tests, and the full media baseline workflow. These checks do not constitute deployment or human acceptance of generated video.
