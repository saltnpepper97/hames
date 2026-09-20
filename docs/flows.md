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

The flow icon appears once a worker transcript is available and stays available
for that chat after completion or cancellation. The drawer keeps the latest flow's
agent tabs through ordinary follow-up messages. Starting another flow clears its
contents and selection; previous flows are not loaded into this view. Normal
session refreshes and worker updates keep the drawer open. Maximize expands it
across the chat area; Restore returns it to sidebar width without switching
transcripts. Workers do not appear as separate entries in the normal chat list.
This view reset does not erase the durable event ledger.

Recipes live in `~/.hames/flow-recipes/<identifier>.json`. The identifier and exact
chat command are displayed on the recipe page. Deleting a recipe preserves chat
history. The former experimental pipeline definitions and ledger records are not
reinterpreted or automatically resumed by this system.

Each worker transcript has a compact message input. Messages go to the selected
worker; they queue while it is busy and start a normal follow-up when it is idle.
Enter sends and Shift+Enter inserts a newline. Drafts stay with their worker tab.
