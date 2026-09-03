import { Show, createEffect, createSignal } from "solid-js";
import { HamesApiError, cancelRun, sendMessage } from "../../api/client";
import type { Session } from "../../api/types";
import { Icon } from "../../shell/icons";
import { ComposerSeat } from "./ComposerSeat";

interface MessageComposerProps {
  session: Session;
  activeRunId?: string;
  onSessionChanged: () => void;
  onSessionUpdated: (session: Session) => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not complete the request.";
}

export function MessageComposer(props: MessageComposerProps) {
  const [draft, setDraft] = createSignal("");
  const [sending, setSending] = createSignal(false);
  const [cancelling, setCancelling] = createSignal(false);
  const [composerError, setComposerError] = createSignal("");
  const [submissionNote, setSubmissionNote] = createSignal("");
  let textarea!: HTMLTextAreaElement;

  const resizeTextarea = () => {
    if (!textarea) return;
    textarea.style.height = "auto";
    const computed = window.getComputedStyle(textarea);
    const lineHeight = Number.parseFloat(computed.lineHeight) || 22;
    const padding = Number.parseFloat(computed.paddingTop) + Number.parseFloat(computed.paddingBottom);
    const maximum = lineHeight * 8 + padding;
    const height = Math.min(textarea.scrollHeight, maximum);
    textarea.style.height = `${height}px`;
    textarea.style.overflowY = textarea.scrollHeight > maximum ? "auto" : "hidden";
  };

  createEffect(() => {
    draft();
    queueMicrotask(resizeTextarea);
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
      props.onSessionChanged();
    } catch (error) {
      setComposerError(errorMessage(error));
    } finally {
      setSending(false);
    }
  };

  const cancel = async () => {
    if (!props.activeRunId || cancelling()) return;
    setCancelling(true);
    setComposerError("");
    try {
      await cancelRun(props.activeRunId);
    } catch (error) {
      setComposerError(errorMessage(error));
    } finally {
      setCancelling(false);
    }
  };

  const reportControlError = (message: string) => {
    setSubmissionNote("");
    setComposerError(message);
  };

  return (
    <div class="composer-dock">
      <div class="composer-shell">
        <textarea
          ref={textarea}
          value={draft()}
          rows={1}
          placeholder="Message Hames"
          aria-label="Message Hames"
          onInput={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
            event.preventDefault();
            void submit();
          }}
        />
        <Show when={composerError() || submissionNote()}>
          <div class="composer-feedback" aria-live="polite">
            <Show when={composerError()} fallback={submissionNote()}>
              <span class="composer-error">{composerError()}</span>
            </Show>
          </div>
        </Show>
        <div class="composer-toolbar">
          <ComposerSeat
            seat="left"
            session={props.session}
            disabled={sending()}
            onSessionUpdated={props.onSessionUpdated}
            onError={reportControlError}
          />
          <div class="composer-toolbar-spacer" />
          <Show when={props.activeRunId}>
            <button
              class="composer-round stop"
              type="button"
              aria-label="Stop"
              title="Stop"
              disabled={cancelling()}
              onClick={() => void cancel()}
            >
              <Icon name="action.stop" size={14} />
            </button>
          </Show>
          <ComposerSeat
            seat="right"
            session={props.session}
            disabled={sending()}
            onSessionUpdated={props.onSessionUpdated}
            onError={reportControlError}
          />
          <button
            class="composer-round send"
            type="button"
            aria-label={props.activeRunId ? "Queue message" : "Send message"}
            title={props.activeRunId ? "Queue message" : "Send message"}
            disabled={!draft().trim() || sending()}
            onClick={() => void submit()}
          >
            <Icon name="action.send" size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
