import { Show, createSignal, onCleanup } from "solid-js";
import { sendMessage } from "../api/client";
import { Button } from "../components/Button";
import { TextArea } from "../components/TextArea";
import { Icon } from "../shell/icons";
import { MessageQueue } from "../chat/components/MessageQueue";
export function WorkerComposer(props: { sessionId: string; agentName: string; revision: string; draft: string; onDraft: (draft: string) => void }) {
  const [sending, setSending] = createSignal(false);
  const [error, setError] = createSignal("");
  const [revision, setRevision] = createSignal(0);
  let disposed = false;
  onCleanup(() => { disposed = true; });
  const submit = async () => {
    const content = props.draft.trim();
    if (!content || sending()) return;
    const sessionId = props.sessionId;
    const updateDraft = props.onDraft;
    setSending(true); setError("");
    try {
      await sendMessage(sessionId, content);
      if (sessionId === props.sessionId && props.draft.trim() === content) updateDraft("");
      if (!disposed && sessionId === props.sessionId) {
        setRevision(value => value + 1);
      }
    } catch (cause) {
      if (!disposed) setError(cause instanceof Error ? cause.message : "Message could not be sent.");
    } finally { if (!disposed) setSending(false); }
  };
  return <div class="worker-composer composer-dock">
    <MessageQueue sessionId={props.sessionId} revision={`${props.revision}:${revision()}`} />
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <TextArea rows={2} resize="none" aria-label={`Message ${props.agentName}`} placeholder={`Message ${props.agentName}…`}
        value={props.draft} onInput={event => props.onDraft(event.currentTarget.value)} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); }
        }} />
      <Button type="submit" variant="bare" class="composer-round send" title={`Send to ${props.agentName}`} aria-label={`Send to ${props.agentName}`} disabled={!props.draft.trim() || sending()} loading={sending()}><Icon name="action.send" size={18} /></Button>
    </form>
    <Show when={error()}><p class="composer-error" role="alert">{error()}</p></Show>
  </div>;
}
