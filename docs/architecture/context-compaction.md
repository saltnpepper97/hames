# Context compaction

Hames owns context compaction rather than delegating continuity policy to an
individual provider. The behavior is therefore the same for local and cloud
providers, while the active session provider, model, and reasoning selection do
the summarization work.

The append-only event ledger remains the complete audit record. Compaction emits
typed started and terminal events; a completed event records the summary, source
event IDs, cutoff, provider provenance, token estimates, pass count, and whether
work remains. The context compiler uses the newest completed summary in place of
only the covered conversation prefix. It never deletes or rewrites transcript
events.

Automatic compaction begins at 80 percent of the compiled input budget, or when
conversation was omitted for budget. Four recent turns remain verbatim. Summary
generation is capped at 2,048 output tokens and three rolling passes, so a large
history is processed in bounded batches without silently dropping an oversized
turn. Failure is durable and the foreground request continues with the compiler's
existing bounded context; cancellation records a distinct cancelled event.

`POST /v1/sessions/{id}/compact` and `/compact` expose the same operation while a
session is idle and its queue is empty. The transcript presents the lifecycle as
one expandable continuity item, and session status reports the most recent
completed compaction.
