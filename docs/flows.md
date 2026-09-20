# Flows

A flow is a saved recipe for a normal coordinator conversation. It has a name, an
identifier, a coordinator agent, and plain-language instructions. There is no
separate pipeline executor or required worker result format.

Create or edit a recipe in **Flows**. The selected coordinator's normal agent
configuration controls its model, tools, and allowed workers; manage those in
**Agents**. A recipe cannot grant new tools or delegation permissions.

Start in the chat composer:

```text
/flow build-review Implement settings search. Preserve unrelated work. Do not push.
```

The coordinator takes over that same conversation, keeping its history. It uses
ordinary delegation, receives worker reports, decides how to proceed, and answers
you normally. Later messages are ordinary chat messages, not deferred pipeline
guidance. Existing approval, cancellation, queue, plan, and delegation behavior
continues to apply. `/flow build-review --plan` approves and executes the current
ready plan through the same coordinator.

A highlighted icon appears while the conversation has ongoing delegated work.
It opens a simple drawer of agent tabs and live transcripts. Luna's coordinating
transcript is the main chat. Each worker has its own tab; earlier work by the same
agent remains available there. A delegation card can reopen a transcript after
completion. Workers do not appear as separate entries in the normal chat list.

Recipes live in `~/.hames/flow-recipes/<identifier>.json`. The identifier and exact
chat command are displayed on the recipe page. Deleting a recipe preserves chat
history. The former experimental pipeline definitions and ledger records are not
reinterpreted or automatically resumed by this system.
