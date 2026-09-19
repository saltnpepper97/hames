import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import { IconArrowUpRight, IconPencil, IconTrash } from "@tabler/icons-solidjs";
import { editQueuedMessage, getMessageQueue, removeQueuedMessage, resumeMessageQueue, sendQueuedMessageNow } from "../../api/client";
import type { MessageQueueState } from "../../api/types";

export function MessageQueue(props: { sessionId: string; revision: string }) {
  const [state, setState] = createSignal<MessageQueueState>();
  const [error, setError] = createSignal("");
  const [pending, setPending] = createSignal(false);
  const [editing, setEditing] = createSignal<{ id: string; original: string; content: string; position: number }>();
  const editPresent = () => state()?.items.some(item => item.id === editing()?.id);
  let editInput: HTMLTextAreaElement | undefined;
  let region: HTMLElement | undefined;
  const closeEditor = () => {
    const editedId = editing()?.id;
    const composer = region?.closest(".composer-dock")?.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message Hames"]');
    setEditing(undefined);
    setError("");
    queueMicrotask(() => {
      const trigger = [...(region?.querySelectorAll<HTMLButtonElement>("[data-edit-id]") ?? [])]
        .find(button => button.dataset.editId === editedId);
      if (trigger?.isConnected) trigger.focus();
      else composer?.focus();
    });
  };
  let generation = 0;
  let disposed = false;
  onCleanup(() => { disposed = true; generation++; });
  const refresh = async (sessionId: string) => {
    const request = ++generation;
    try {
      const next = await getMessageQueue(sessionId);
      if (!disposed && request === generation && sessionId === props.sessionId) {
        setState(next);
        setError("");
      }
    } catch (cause) {
      if (!disposed && request === generation && sessionId === props.sessionId)
        setError(cause instanceof Error ? cause.message : "Unable to load queued messages.");
    }
  };
  let currentSession = "";
  createEffect(() => {
    const sessionId = props.sessionId;
    props.revision;
    if (currentSession !== sessionId) {
      currentSession = sessionId;
      setState(undefined);
      setError("");
      setPending(false);
      setEditing(undefined);
    }
    void refresh(sessionId);
  });
  const act = async (action: (sessionId: string) => Promise<unknown>) => {
    if (pending()) return;
    const sessionId = props.sessionId;
    setPending(true);
    setError("");
    try {
      await action(sessionId);
      if (!disposed && sessionId === props.sessionId) { await refresh(sessionId); return true; }
    } catch (cause) {
      if (!disposed && sessionId === props.sessionId)
        setError(cause instanceof Error ? cause.message : "Unable to update queued messages.");
    } finally {
      if (!disposed && sessionId === props.sessionId) setPending(false);
    }
  };
  const save = async () => {
    const edit = editing();
    if (!edit || !editPresent()) return;
    const saved = await act(id => editQueuedMessage(id, edit.id, edit.content, edit.original));
    if (saved) closeEditor();
  };
  return <>
    <Show when={state()?.session_id === props.sessionId && (state()!.items.length > 0 || editing())}>
      <section class="composer-queue" aria-label="Queued messages" ref={region}>
        <div class="composer-queue-heading">
          <span>Queued <span class="composer-queue-count">{state()!.items.length}/3</span>{state()!.paused ? " · Paused" : ""}</span>
          <Show when={state()!.items.length >= 3}><span>Full</span></Show>
          <Show when={state()!.paused}>
            <button type="button" disabled={pending()} onClick={() => void act(resumeMessageQueue)}>Resume queue</button>
          </Show>
        </div>
        <ol>
          <For each={state()!.items}>{item => <li classList={{ "is-editing": editing()?.id === item.id }}>
            <span class="composer-queue-position" aria-hidden="true">{item.position}</span>
            <span class="composer-queue-preview" title={item.content}>{item.content || `${item.attachments.length} attachment${item.attachments.length === 1 ? "" : "s"}`}</span>
            <Show when={item.content && item.attachments.length}><span class="composer-queue-files" title="Attachments retained">+{item.attachments.length} file{item.attachments.length === 1 ? "" : "s"}</span></Show>
            <div class="composer-queue-actions">
              <button type="button" disabled={pending() || !!editing()} class="composer-queue-steer" aria-label={`Steer with queued message ${item.position}`} title="Steer: stop the current response and start this message next; keep the rest of the queue" onClick={() => void act(id => sendQueuedMessageNow(id, item.id))}><IconArrowUpRight size={15} strokeWidth={1.7} aria-hidden="true" /><span>Steer</span></button>
              <button type="button" disabled={pending() || !!editing()} aria-label={`Edit queued message ${item.position}`} title="Edit queued message" data-edit-id={item.id} onClick={() => {
                setError("");
                setEditing({ id: item.id, original: item.content, content: item.content, position: item.position });
                queueMicrotask(() => { editInput?.focus(); editInput?.setSelectionRange(editInput.value.length, editInput.value.length); });
              }}><IconPencil size={15} strokeWidth={1.7} aria-hidden="true" /></button>
              <button type="button" disabled={pending() || !!editing()} class="composer-queue-delete" aria-label={`Delete queued message ${item.position}`} title="Delete queued message" onClick={() => void act(id => removeQueuedMessage(id, item.id))}><IconTrash size={15} strokeWidth={1.7} aria-hidden="true" /></button>
            </div>
          </li>}</For>
        </ol>
        <Show when={editing()}>
          <div class="composer-queue-editor">
            <label for="queued-message-edit">Edit queued message {editing()!.position}</label>
            <textarea id="queued-message-edit" ref={editInput} value={editing()!.content} disabled={pending()}
              onInput={event => setEditing(current => current && ({ ...current, content: event.currentTarget.value }))}
              onKeyDown={event => {
                if (event.key === "Escape" && !pending()) { event.preventDefault(); closeEditor(); }
                if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void save(); }
              }} />
            <Show when={!editPresent()}><p role="status">This message already started or was removed. Your edit has not been sent.</p></Show>
            <div class="composer-queue-editor-actions">
              <button type="button" disabled={pending()} onClick={closeEditor}>Cancel</button>
              <button type="button" class="composer-queue-save" disabled={pending() || !editPresent() || (!editing()!.content.trim() && !state()!.items.find(item => item.id === editing()!.id)?.attachments.length)} onClick={() => void save()}>Save</button>
            </div>
          </div>
        </Show>
      </section>
    </Show>
    <Show when={error()}><p class="composer-queue-error" role="alert">{error()}</p></Show>
  </>;
}
