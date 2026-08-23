# M7 Qwen3.8 autonomous-Skills acceptance

Date: 2026-08-23

- Provider: live local llama.cpp
- Model: `qwen3.8-27b`
- Reasoning: `medium`
- Context window: 131072 tokens
- Hames protocol: v9
- Isolated state: `/tmp/hames-m7-live.tvVhe8`

## Repeated-workflow trigger

The live session performed a two-tool repository cross-check using `read_file` and
`list_dir`. A loosely worded follow-up correctly stayed below the configured task
similarity threshold. Repeating the materially identical workflow crossed the
default recurrence threshold and created background job:

```text
e9de0943-6b76-48b9-b804-f37278087920  author  completed  attempts 1
```

The user did not approve or promote a proposal. Hames autonomously drafted,
validated, independently evaluated, and activated:

```text
cross-verify-repo-claims v1
scope: workspace
tools: read_file, list_dir
content hash: 0f0ac407896d1d1faf51610f131c795086c6c1eb140dafe253648cdb10695a37
```

The generated procedure was grounded in both observed tools and preserved the
important caveat that a directory listing corroborates present toolchains but
cannot independently establish a historical “first client” designation.

## Progressive loading

The next matching prompt was:

```text
Cross-check the README claims against the directory structure and verify which
language owns the backend and what client is first.
```

Qwen recognized the catalog entry and emitted:

```text
tool> requested skill_load
tool> skill_load: loaded Skill cross-verify-repo-claims v1
```

It then followed the learned procedure, called `read_file` and `list_dir`, and
returned a per-claim cross-check. The context manifest attributed the two stages
separately:

```text
181 tokens  skill/skill_catalog  cross-verify-repo-claims v1 · workspace · score 1.000
293 tokens  skill/skill          cross-verify-repo-claims v1 · workspace
```

## Defect found during acceptance

The first attempt to run the post-activation task exposed an M6 accounting bug:
memory retrieval estimated only summary bytes while the compiler included the
richer canonical record with subject, predicate, value, and provenance. Several
long episodic records could therefore pass retrieval selection and then exceed the
compiler's memory budget.

The estimator now budgets the exact canonical provenance-bearing record shape plus
list overhead. A regression test creates multiple detailed records, verifies the
selected canonical payload remains within budget, and compiles it successfully.
After restarting the isolated protocol-v9 gateway with the fix, the same live
session loaded and used the Skill successfully.

## Result

The M7 local-provider gate passed:

- deterministic recurrence detection;
- recoverable autonomous job execution;
- live Qwen drafting and independent evaluation;
- automatic activation without a user proposal inbox;
- immutable workspace-scoped package and hash;
- relevant compact catalog selection;
- explicit progressive `skill_load`;
- attributed catalog and loaded-procedure token costs;
- learned procedure use on a subsequent task.

The isolated gateway was stopped after the run. Its state was left in `/tmp` for
post-run inspection and normal temporary-directory cleanup.
