# Autonomous Skills

Skills are Hames procedural memory. They describe how an agent should perform a
reusable workflow; they are not facts, plugins, permissions, or registered
projects.

The trusted Python backend owns the complete lifecycle. Models may draft and
evaluate package content, but cannot write directly to the registry, activate a
version, grant tools, or bypass policy. Hames validates model output, stores an
immutable package, links exact evidence, and makes the activation decision.

## Learning loop

```text
settled runs
  -> deterministic workflow signatures
  -> repeated-success or explicit correction trigger
  -> recoverable author/patch job
  -> grounded draft
  -> deterministic validation and isolated script self-test
  -> independent model evaluation
  -> autonomous activation or durable rejection
  -> catalog -> load -> use -> settled outcome
  -> correction, quarantine, or later replacement
```

This loop is deliberately autonomous. Pin, archive, rollback, history, evidence,
and job inspection let a user constrain or audit it without turning normal
evolution into manual inbox work.

The chat runtime exposes `skill_catalog` for inspection and `skill_control` for
pin, unpin, archive, restore, and rollback. These operations call the same
versioned registry used by the gateway; the model never edits package files or
registry tables directly. Archive and rollback are approval-gated.

## Authority boundary

A Skill declares which existing Hames tools its procedure expects. Declaration
does not grant those tools. The active agent capsule, trust record, runtime policy,
and one-shot approval rules still decide every invocation.

Self-authored scripts have an even narrower boundary: they run only through
`skill_run` in an offline Bubblewrap namespace. The package and project are
read-only; only disposable run scratch is writable. Missing isolation is a hard
rejection.

## Context boundary

The context compiler first includes compact relevant catalog entries. A catalog
entry is descriptive data, not an instruction source. The model loads a selected
Skill explicitly; only then does its full procedure enter the next model request.
Loaded procedures remain subordinate to the core contract and policy.

Every catalog decision, load, execution, and outcome is attributable in the event
ledger and context manifest.
