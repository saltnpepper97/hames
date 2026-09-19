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

Automatic compaction begins at `context.compaction_auto_threshold_ratio` of the
compiled input budget (default 80 percent), or sooner if
`context.compaction_auto_threshold_tokens` is set, or when conversation was
omitted for budget. Recent turns stay verbatim; if the session has fewer turns
than that preserve count, compaction still folds every turn except the latest.
Summary generation is capped at 2,048 output tokens, with reasoning disabled
(`off`, mapped to Codex `none`) so local thinking models still emit a usable
summary, and three rolling passes, so
a large history is processed in bounded batches without silently dropping an
oversized turn. Failure is durable and the foreground request continues with the
compiler's existing bounded context; cancellation records a distinct cancelled
event. Hames performs this for local and cloud providers.

`POST /v1/sessions/{id}/compact` and `/compact` expose the same operation while a
session is idle and its queue is empty. The transcript presents the lifecycle as
one expandable continuity item, and session status reports the most recent
completed compaction.

Automatic recovery also runs when context compilation would otherwise exceed the
input budget, and may repeat throughout a single run. Long active turns are
checkpointed at completed response/tool-batch boundaries. The newest completed
exchange normally stays verbatim; budget exhaustion can checkpoint it too. An
incomplete tool batch is never split. The original user request is retained
verbatim across checkpoints, alongside the rolling summary and remaining tool
protocol. Advancing a cutoff also summarizes intervening older turns so no gap
in history is silently skipped. Raw ledger events remain intact.

Local provider sessions with a fallback context window refresh it from model
metadata before compiling requests. Explicit profile limits are preserved. A
single request or exchange too large for even the summarizer remains an explicit
budget error; failed summarization never commits a cutoff or discards history.

### Approved plans in delegated work

When a plan execution delegates work, the harness captures the exact approved plan revision and user execution note in the child’s durable task card. The delegating model’s task text defines the assigned portion; it does not replace the authoritative plan. Nested delegations inherit the same captured revision. The coordinator supplies only a short assignment (the whole plan or a named portion); it must not spend output tokens copying, summarizing, or rewriting the plan into the tool call. Context compilation includes the full plan as mandatory task context after compaction, with its token cost included in the input budget. An unrelated later root run does not inherit an earlier execution’s approval. Existing child task cards are not rewritten retroactively.
