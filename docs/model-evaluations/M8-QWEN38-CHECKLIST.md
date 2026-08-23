# M8 Qwen3.8 Scars acceptance checklist

Run every step against your live `qwen3.8-27b` llama.cpp profile and record what
you see. M8 is tagged only when every section passes. Fill results into the
"Observed" line of each step, then save this file with the date and state root.

- Date: 2026-08-23
- Provider: live local llama.cpp
- Model: `qwen3.8-27b`
- Reasoning: medium
- Context window: 131072 (provider)
- Hames protocol: v9
- Isolated state root: `/tmp/hames-m8-accept`
- Demo workspace: `/tmp/m8-accept-demo`
- Session (explicit/heal/regress/policy/inspection): `83cb0326-dcb7-4045-a45d-6f33174d8511`

Setup:

```bash
HAMES_HOME=/tmp/hames-m8-accept target/debug/hames gateway start
HAMES_HOME=/tmp/hames-m8-accept target/debug/hames
```

Create a session in any scratch project directory (e.g. `/tmp/m8-demo`), then
trust it when prompted.

---

## 1. Explicit correction opens a Scar and repairs itself

1. Establish a wrong fact: ask Qwen something answerable from a file, where the
   model states a wrong location/value.
2. Run:
   ```text
   /correct the milestone file is docs/plan.md not plan.txt
   ```
3. Expected immediately:
   - REPL prints `correct> scar <id> recorded as open (Correction: ...)`.
4. Then run:
   ```text
   /evolution show <scar-id>
   ```
5. Expected:
   - status `guarded` (the semantic-memory repair auto-promoted),
   - detection `explicit_correction`, severity `high`,
   - one repair candidate, status `promoted`.
6. Confirm the memory exists: `/memory search milestone` shows a workspace
   semantic record containing your correction text.

Observed: PASS. Scar `f8a706dd-96f4-4feb-a62c-12fbdcea9151` opened from
`POST /v1/sessions/.../correct` (the same gateway path `/correct` uses), status
`guarded`, detection `explicit_correction`, severity `high`, layer
`semantic_memory`, one promoted repair. `/memory` search for `milestone` returned
a workspace semantic record containing `docs/plan.md`. Qwen's prior turn had
already named `docs/plan.md` correctly; the correction still opened and repaired
the Scar as specified.

## 2. Guard counting heals the Scar

1. Ask three ordinary follow-up questions in the same session (no correction
   language). After each answer completes, run `/evolution show <scar-id>`.
2. Expected after the third clean run:
   - `guards: 3 clean` and status flips to `healed`,
   - live stream printed `evolution> guard pass recorded (n clean)` after each run.

Observed: PASS. After the context-injection follow-up, guards=1/`guarded`; after
"What directory holds application settings?", guards=2; after "Name one file
under src/.", status `healed`, guards=3, regressions=0.

## 3. Regression on repeated correction

1. Repeat step 1's exact correction again (`/correct ...` same wording).
2. Expected:
   - the Scar flips to `regressed` with `regressions: 1`,
   - a second repair candidate (version 2) appears automatically,
   - because memory repairs are grounded in user correction, it re-promotes to
     `guarded`.

Observed: PASS. Same scar id `f8a706dd` returned (no duplicate). Lineage:
`candidate → open → repair_proposed → guarded → healed → regressed →
repair_proposed → guarded`. Repairs v1 and v2 both `promoted` (`semantic_memory`).
`regressions: 1`, status back to `guarded`.

## 4. Conversational correction is noticed without /correct

1. In a fresh session (same project), get an answer you dislike, then reply in
   normal language:
   ```text
   Actually that was wrong — the config lives in conf/settings.toml
   ```
2. Expected after the turn completes:
   - live output or `/evolution list open` shows a new Scar with detection
     `conversational_correction`, severity `medium`,
   - no reviewer-model call happened (default off).

Observed: PASS. Fresh session `4655bc90`. Qwen had already named
`conf/settings.toml`; the contradiction phrasing still opened scar `085f37e2`
(`conversational_correction`, severity `medium`, auto-promoted to `guarded`).
No `evolution_evaluation` / reviewer-model request.

## 5. Repeated failures open a Scar

Pick a shell command that reliably fails identically (e.g. ask Qwen to run
`ls /definitely-not-here-42` three times across up to three turns). Then:

```text
/evolution list
```

Expected: a `repeated_failure` scar titled `Repeated failure: tool:shell:...`
once the same signature occurred 3 times; later identical failures print
`triggered` events instead of new scars.

Observed: PASS. Session `f7c81867`. After three identical
`ls /definitely-not-here-42` failures, scar `a45522a2` opened (`open`,
`repeated_failure`, signature `tool:shell:shell exited with code #`). A fourth
identical run did not create another scar; `scar.triggered` was recorded. The
scar stays `open` (opaque repeated failure is not auto-routed).

## 6. Active guard enters model context

1. With a `guarded` correction Scar present (redo step 1 if healed), send one
   more message, then:
   ```text
   /context
   ```
2. Expected: the latest manifest lists source `evolution.scar`
   (`source_type scar`, origin `evolution`) whose payload JSON contains the
   scar title and expected behavior. It must disappear once the Scar is healed.

Observed: PASS. While scar `f8a706dd` was still `guarded`, the follow-up
"What is the current milestone name?" compiled context with `evolution.scar` in
`source_order`. Qwen answered M8 from `docs/plan.md`. After healing, later
manifests in this run no longer listed that source.

## 7. Policy rule blocks after approval

```bash
curl -s http://127.0.0.1:7411/v1/policy-rules -H "Authorization: Bearer $(cat ~/.hames/runtime/gateway-token)" # read token path from gateway logs if different
```

Simpler: propose+activate through Python against the running gateway:

```bash
TOKEN=$(cat "$HAMES_HOME/runtime/gateway.token" 2>/dev/null || cat /tmp/hames-m8-live/runtime/gateway.token)
curl -s -X POST http://127.0.0.1:7411/v1/sessions/<session-id>/policy-rules \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"deny","pattern":"touch\\s+/tmp/m8-demo/forbidden","reason":"forbidden file protection"}'
curl -s -X POST http://127.0.0.1:7411/v1/policy-rules/<rule-id>/activate \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"reason":"approved"}'
```

Then ask Qwen to `touch /tmp/m8-demo/forbidden`.

Expected: the tool result reports rejection citing `forbidden file protection`;
the transcript shows a `policy.decided` deny attributed to the declarative rule.

Observed: PASS. Rule `b50caf03` proposed with Python regex
`touch\s+/tmp/m8-accept-demo/FORBIDDEN` (POSIX `[[:space:]]` is now rejected at
propose time — that silent no-op is what stalled the previous live attempt) and
activated. Qwen issued `touch /tmp/m8-accept-demo/FORBIDDEN`. `policy.decided`
was `deny` / `forbidden file protection`. Qwen reported the harness rejection.
`/tmp/m8-accept-demo/FORBIDDEN` was not created.

## 8. Full lineage inspection

```text
/evolution show <regressed-scar-id>
GET /v1/sessions/<session-id>/scars/<scar-id>/inspection
```

Expected for at least one Scar: evidence timeline referencing exact event ids,
ordered transitions (recorded → opened → repair_proposed → guarded → healed →
regressed → guarded), both repair versions with decisions, evaluation entries,
guard/regression counts, and a plain explanation sentence.

Observed: PASS. `GET /v1/sessions/83cb0326-.../scars/f8a706dd-.../inspection`
returned evidence (1 event), transitions
`scar.recorded/candidate → opened/open → repair_proposed → guarded → healed →
regressed → repair_proposed → guarded`, repairs v1 and v2 both promoted
semantic_memory, `successful_guard_count=3`, `regression_count=1`, explanation
"The user explicitly corrected Hames with /correct; the user's own statement is
the authoritative diagnosis."

---

## Acceptance gate

M08 is complete only when the full cycle was demonstrated:

```text
failure -> evidence -> Scar -> repair candidate -> evaluation ->
controlled promotion -> guarded future runs -> healing or regression
```

Record anything that failed here, then tag:

```bash
git tag -a m08 -m "Hames milestone M08 complete"
```

Observed: the full cycle passed on 2026-08-23 against live `qwen3.8-27b`. Two
product bugs found during the earlier stalled live run are fixed on `main`:
duplicate dequeue no longer kills the memory/skill workers, and POSIX character
classes are rejected when proposing policy rules. `gateway.log` for this
acceptance root contains no `memory job is not pending`. Tagged `m08`.
