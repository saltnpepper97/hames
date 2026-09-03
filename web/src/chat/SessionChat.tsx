import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import { Dynamic } from "solid-js/web";
import { HamesApiError, cancelRun, sendMessage } from "../api/client";
import type { Session } from "../api/types";
import { useWebPlugins } from "../shell/pluginContext";
import { projectConversation, withLiveOutput } from "./projection";
import { createSessionStream } from "./sessionStream";

interface SessionChatProps {
  session: Session;
  onSessionChanged: () => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not complete the request.";
}

export function SessionChat(props: SessionChatProps) {
  const plugins = useWebPlugins();
  const stream = createSessionStream(() => props.session.id);
  const durableProjection = createMemo(() => projectConversation(stream.events()));
  const projection = createMemo(() => withLiveOutput(durableProjection(), stream.liveOutput()));
  const [draft, setDraft] = createSignal("");
  const [sending, setSending] = createSignal(false);
  const [cancelling, setCancelling] = createSignal(false);
  const [composerError, setComposerError] = createSignal("");
  const [submissionNote, setSubmissionNote] = createSignal("");
  let transcript!: HTMLDivElement;
  let stickToBottom = true;

  createEffect(() => {
    projection().nodes.length;
    stream.liveOutput()?.text;
    stream.liveOutput()?.reasoning;
    queueMicrotask(() => {
      if (stickToBottom && transcript) transcript.scrollTop = transcript.scrollHeight;
    });
  });

  const submit = async () => {
    const content = draft().trim();
    if (!content || sending()) return;
    setSending(true);
    setComposerError("");
    setSubmissionNote("");
    try {
      const accepted = await sendMessage(props.session.id, content);
      setDraft("");
      setSubmissionNote(accepted.disposition === "queued" ? "Message queued" : "Message sent");
      stickToBottom = true;
      props.onSessionChanged();
    } catch (error) {
      setComposerError(errorMessage(error));
    } finally {
      setSending(false);
    }
  };

  const cancel = async () => {
    const runId = projection().activeRunId;
    if (!runId || cancelling()) return;
    setCancelling(true);
    setComposerError("");
    try {
      await cancelRun(runId);
    } catch (error) {
      setComposerError(errorMessage(error));
    } finally {
      setCancelling(false);
    }
  };

  const handleComposerKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    void submit();
  };

  return (
    <div class="session-chat">
      <header class="chat-header">
        <div>
          <h1 id="chat-title">{props.session.title?.trim() || "New chat"}</h1>
          <p>
            {props.session.agent_id} <span aria-hidden="true">·</span> {props.session.model}
          </p>
        </div>
        <span class="stream-state" data-state={stream.state()}>
          {stream.state() === "live"
            ? projection().activeRunId
              ? "Working"
              : "Live"
            : stream.state() === "connecting"
              ? "Loading history"
              : "Reconnecting"}
        </span>
      </header>

      <div
        class="transcript-scroll"
        ref={transcript}
        onScroll={() => {
          stickToBottom =
            transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 96;
        }}
      >
        <div class="transcript-column" aria-live="polite">
          <Show
            when={projection().nodes.length > 0}
            fallback={
              <div class="conversation-empty compact">
                <h2>
                  {stream.state() === "connecting"
                    ? "Loading conversation…"
                    : "Start a conversation"}
                </h2>
                <p>
                  {stream.state() === "connecting"
                    ? "Reading durable events from the local gateway."
                    : "Messages sent here run through the same Hames session as the TUI and REPL."}
                </p>
              </div>
            }
          >
            <For each={projection().nodes}>
              {(node) => {
                const contribution = plugins.conversationNodes.get(node.kind);
                return contribution ? (
                  <Dynamic component={contribution.component} node={node} />
                ) : null;
              }}
            </For>
          </Show>
        </div>
      </div>

      <div class="composer-dock">
        <div class="composer-shell">
          <textarea
            value={draft()}
            rows={1}
            placeholder="Message Hames"
            aria-label="Message Hames"
            onInput={(event) => setDraft(event.currentTarget.value)}
            onKeyDown={handleComposerKeyDown}
          />
          <div class="composer-actions">
            <div class="composer-feedback" aria-live="polite">
              <Show when={composerError()} fallback={submissionNote()}>
                <span class="composer-error">{composerError()}</span>
              </Show>
            </div>
            <Show when={projection().activeRunId}>
              <button
                class="button quiet"
                type="button"
                disabled={cancelling()}
                onClick={() => void cancel()}
              >
                {cancelling() ? "Stopping…" : "Stop"}
              </button>
            </Show>
            <button
              class="button primary"
              type="button"
              disabled={!draft().trim() || sending()}
              onClick={() => void submit()}
            >
              {sending() ? "Sending…" : projection().activeRunId ? "Queue" : "Send"}
            </button>
          </div>
        </div>
        <p class="composer-hint">Enter to send · Shift+Enter for a new line</p>
      </div>
    </div>
  );
}
