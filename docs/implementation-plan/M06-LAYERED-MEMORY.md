# M06 — Layered Memory: Semantic, Relationship, Operational, and Episodic

## Goal

Give Hames durable memory that helps real work without turning every past utterance into undifferentiated vector recall.

M06 implements one scoped memory store with four layers:

1. semantic facts;
2. typed relationships;
3. active operational work;
4. searchable episodic history.

Procedural knowledge remains the responsibility of skills in M07.

## Memory scope model

Every durable memory record has one scope:

```text
global
project:<project-id>
agent:<agent-id>
team:<session-or-team-id>
```

Scratch state is session-local and stays in the event/session layer rather than durable memory.

### Default visibility

- global facts: readable by all agents unless policy restricts;
- project facts: readable by agents operating in that project;
- agent-private facts: readable by that agent;
- team/session facts: readable only by participants in that temporary scope.

Promotion from narrower scope to broader scope is an explicit memory operation and emits provenance.

Agents may propose broad-scope writes but do not silently broaden private hypotheses.

## A. Semantic memory

Store durable factual assertions.

Required fields:

```text
id
subject
predicate
value_json
scope
confidence
importance
status
valid_from
valid_until nullable
supersedes_id nullable
created_at
created_by_agent nullable
source_event_ids
```

Statuses:

```text
active
superseded
retracted
proposed
```

Facts are not destructively edited. A correction creates a new record and supersedes/retracts old record.

Examples:

```text
project:rootlatch → current_milestone → "physical Pico transition test"
user → prefers → "simple agent harnesses"
project:halley → configuration_language → project:rune
```

Do not infer sensitive personal attributes merely because they appeared in conversation. Memory-write policy supports excluded categories.

## B. Relationship memory

Tables:

```text
entities
relationships
```

Entity fields:

```text
id
canonical_name
type
scope
aliases
source_event_ids
status
```

Relationship fields:

```text
id
source_entity_id
relation
target_entity_id
scope
confidence
valid_from
valid_until
source_event_ids
status
```

Support:

- lookup by canonical name/alias;
- outgoing/incoming edges;
- bounded traversal up to configurable depth, default 2;
- temporal validity;
- relationship supersession/retraction.

Do not introduce external graph database for v0.1.

## C. Operational memory

This is the everyday work layer.

`work_items` contain:

```text
id
title
description
project_id
owner_agent_id nullable
status
priority
parent_id nullable
blocked_by[]
constraints[]
next_action nullable
created_at
updated_at
source_event_ids
closed_at nullable
result_summary nullable
```

Statuses:

```text
active
blocked
waiting
completed
cancelled
superseded
```

Operational records are editable state, unlike append-only historical events, but every mutation emits a work event containing before/after version IDs.

Active/blocked/waiting work is automatically considered by context compiler when project/agent scopes match.

Completed items stop being auto-injected and remain searchable.

## D. Episodic memory

Build episodic memory as a projection from settled runs/sessions, not another raw transcript copy.

When run/session closes, create/update episode record containing:

```text
episode_id
session_id
project_id
agent_ids
user_request_summary
final_outcome
tool_names
error_signatures
correction_event_ids
started_at
completed_at
search_text
```

First implementation should derive summaries deterministically from user request/final answer/tool metadata where possible. If an LLM summary is used, it must be an explicit model call with usage/events.

The full event ledger remains authoritative detail.

## Memory write API

Core operations:

```text
memory.propose
memory.accept
memory.reject
memory.supersede
memory.retract
work.create
work.update
work.close
relationship.create/supersede
```

The agent may write only to scopes allowed by `AGENT.md` and policy.

High-confidence user-explicit facts may be accepted automatically according to configurable rules. Model-inferred facts default to proposed or lower confidence.

## Retrieval

Implement hybrid retrieval without requiring external services.

### Stage 1 — scope filter

Eliminate inaccessible records before ranking.

### Stage 2 — structured direct matches

Boost:

- current project;
- current agent;
- active work;
- direct entity names/aliases;
- direct graph neighbours.

### Stage 3 — FTS5

Use FTS5 over semantic values, entity names, relationships, work text, and episode search text.

### Stage 4 — optional embeddings

Implement embedding-provider interface.

At least one OpenAI-compatible embedding adapter must work when configured.

Store embeddings locally as float32 blobs with model/version metadata and brute-force cosine retrieval suitable for personal-scale stores. No external vector database.

If embeddings are unconfigured, retrieval remains fully functional through structured + FTS retrieval.

### Stage 5 — merge/rerank

Combine candidates using deterministic method such as reciprocal-rank fusion, then adjust by:

- scope specificity;
- confidence;
- importance;
- active operational status;
- recency where appropriate;
- contradiction/supersession state.

Diversify near-duplicates.

## Context integration

Add sources:

```text
memory.semantic
memory.relationship
memory.operational
memory.episodic
```

Default injection:

- semantic: relevant high-confidence facts;
- relationship: useful local graph facts only;
- operational: relevant active/blocked work within budget;
- episodic: conservative, only when strongly relevant or explicitly searched.

Every included memory item exposes provenance in context manifest.

## Memory tools

Add model tools:

```text
memory_search
memory_remember
memory_correct
work_list
work_update
relationship_query
```

These respect agent scopes/policy.

CLI:

```bash
hames memory search ...
hames memory show ...
hames memory retract ...
hames work list
```

## Memory events

Add at minimum:

```text
memory.retrieved
memory.proposed
memory.accepted
memory.rejected
memory.superseded
memory.retracted
relationship.changed
work.changed
episode.created
embedding.created
```

Retrieval events record candidates considered/selected with scores and scope decisions, subject to redaction.

## Web Inspector additions

Read-only memory panels:

- memory records;
- provenance events;
- scope;
- status/confidence;
- retrieval candidates for model request;
- selected vs dropped memories;
- supersession chain;
- relationship neighbourhood;
- active work.

## Tests

Cover:

- scope isolation;
- project sharing between authorized agents;
- agent-private isolation;
- supersession without historical mutation;
- relationship traversal and temporal validity;
- operational status transitions;
- completed work no longer auto-injected;
- FTS retrieval;
- optional embedding retrieval with fake embedding provider;
- deterministic hybrid ranking;
- inaccessible memories excluded before ranking;
- episodic projection after session close;
- memory provenance links;
- context budget behavior;
- sensitive-category memory policy;
- process restart persistence;
- migration from M05.

## Manual smoke test

1. Tell Hames a stable project fact and verify it becomes memory under allowed policy.
2. Start another authorized agent in same project and verify shared fact retrieval.
3. Store one agent-private tactic and verify another agent cannot see it.
4. Create active work item; verify it appears in relevant context.
5. Complete work item; verify it stops being auto-injected.
6. Correct original project fact; verify old fact stays historically visible but inactive.
7. Inspect why new fact was retrieved in Web Inspector.

## Commit expectations

Split this large milestone:

1. memory scope/common schema;
2. semantic facts/supersession;
3. entities/relationships;
4. operational work;
5. episodic projection;
6. FTS/embedding retrieval;
7. context/tool integration;
8. inspector/docs/tests.

## Acceptance gate

M06 is complete when memory is useful across sessions and agents, scope-safe, correctable, provenance-backed, searchable without embeddings, optionally semantically enhanced with embeddings, and fully inspectable.

A raw “store every message embedding” shortcut does not qualify.

Finish clean and tag `m06`.
