import { For, Show, createSignal } from "solid-js";
import type { JSX } from "solid-js";
import { HamesApiError, answerQuestion, resolveApproval } from "../../api/client";
import type { ConversationNode } from "../../chat/projection";
import type { ConversationNodeContribution } from "../../shell/plugins";

function ToolNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "tool") return <></>;
  const node = props.node;
  const detail = () => {
    if (node.content) return node.content;
    if (node.arguments) return JSON.stringify(node.arguments, null, 2);
    return "No details were recorded.";
  };

  return (
    <details class="tool-node">
      <summary>
        <span>{node.name}</span>
        <span class={`tool-state ${node.status}`}>{node.status}</span>
        <Show when={node.summary}>
          <span class="tool-summary">{node.summary}</span>
        </Show>
      </summary>
      <pre>{detail()}</pre>
    </details>
  );
}

function ReasoningNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "reasoning") return <></>;
  const node = props.node;
  return (
    <details class="reasoning-node" open={node.live || undefined}>
      <summary>{node.live ? "Thinking" : "Reasoning"}</summary>
      <div class="message-copy">{node.content}</div>
    </details>
  );
}

function NoticeNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "notice") return <></>;
  return <p class={`conversation-notice ${props.node.tone}`}>{props.node.content}</p>;
}

function MessageNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "user" && props.node.kind !== "assistant") return <></>;
  const node = props.node;
  return (
    <article class={`message-node ${node.kind}`}>
      <div class="message-role">{node.kind === "user" ? "You" : "Hames"}</div>
      <div class="message-copy">{node.content}</div>
      <Show when={node.live}>
        <span class="live-cursor" aria-label="Streaming response" />
      </Show>
    </article>
  );
}

function mutationError(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not complete the request.";
}

function ApprovalNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "approval") return <></>;
  const node = props.node;
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");

  const decide = async (decision: "approved" | "approved_session" | "denied") => {
    if (busy() || node.status !== "pending") return;
    setBusy(true);
    setError("");
    try {
      await resolveApproval(node.approvalId, node.requestHash, decision);
    } catch (caught) {
      setError(mutationError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section class="decision-card approval-card" aria-label={`Approval for ${node.name}`}>
      <div class="decision-heading">
        <div>
          <span class="eyebrow">Approval required</span>
          <h2>{node.name}</h2>
        </div>
        <span class="decision-status">{node.status}</span>
      </div>
      <Show when={node.reason}>
        <p>{node.reason}</p>
      </Show>
      <details>
        <summary>Request details</summary>
        <pre>{JSON.stringify(node.arguments, null, 2)}</pre>
      </details>
      <Show when={node.status === "pending"}>
        <div class="decision-actions">
          <button class="button quiet" type="button" disabled={busy()} onClick={() => void decide("denied")}>
            Deny
          </button>
          <Show when={node.allowSession}>
            <button class="button" type="button" disabled={busy()} onClick={() => void decide("approved_session")}>
              Allow for session
            </button>
          </Show>
          <button class="button primary" type="button" disabled={busy()} onClick={() => void decide("approved")}>
            Allow once
          </button>
        </div>
      </Show>
      <Show when={error()}>
        <p class="decision-error" role="alert">{error()}</p>
      </Show>
    </section>
  );
}

function QuestionNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "question") return <></>;
  const node = props.node;
  const [note, setNote] = createSignal("");
  const [customAnswer, setCustomAnswer] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");

  const choose = async (selectedOption: string) => {
    if (busy() || node.status !== "pending") return;
    setBusy(true);
    setError("");
    try {
      await answerQuestion(node.questionId, {
        selected_option: selectedOption,
        note: note().trim(),
        custom_answer: "",
      });
    } catch (caught) {
      setError(mutationError(caught));
    } finally {
      setBusy(false);
    }
  };

  const submitCustom = async () => {
    const answer = customAnswer().trim();
    if (!answer || busy() || node.status !== "pending") return;
    setBusy(true);
    setError("");
    try {
      await answerQuestion(node.questionId, {
        selected_option: null,
        note: "",
        custom_answer: answer,
      });
    } catch (caught) {
      setError(mutationError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section class="decision-card question-card" aria-label="Agent question">
      <div class="decision-heading">
        <div>
          <span class="eyebrow">Agent question</span>
          <h2>{node.question}</h2>
        </div>
        <span class="decision-status">{node.status}</span>
      </div>
      <Show when={node.status === "pending"} fallback={<p class="decision-answer">{node.answer}</p>}>
        <div class="question-options">
          <For each={node.options}>
            {(option) => (
              <button type="button" disabled={busy()} onClick={() => void choose(option.label)}>
                <strong>{option.label}</strong>
                <Show when={option.description}><span>{option.description}</span></Show>
              </button>
            )}
          </For>
        </div>
        <Show when={node.options.length > 0}>
          <textarea
            class="decision-input"
            value={note()}
            placeholder="Optional note with your choice"
            aria-label="Option note"
            onInput={(event) => setNote(event.currentTarget.value)}
          />
        </Show>
        <div class="custom-answer">
          <textarea
            class="decision-input"
            value={customAnswer()}
            placeholder="Or write a custom answer"
            aria-label="Custom answer"
            onInput={(event) => setCustomAnswer(event.currentTarget.value)}
          />
          <button
            class="button primary"
            type="button"
            disabled={!customAnswer().trim() || busy()}
            onClick={() => void submitCustom()}
          >
            Send answer
          </button>
        </div>
      </Show>
      <Show when={error()}>
        <p class="decision-error" role="alert">{error()}</p>
      </Show>
    </section>
  );
}

export const coreConversationNodes = [
  { kind: "user", component: MessageNode },
  { kind: "assistant", component: MessageNode },
  { kind: "reasoning", component: ReasoningNode },
  { kind: "tool", component: ToolNode },
  { kind: "notice", component: NoticeNode },
  { kind: "approval", component: ApprovalNode },
  { kind: "question", component: QuestionNode },
] satisfies readonly ConversationNodeContribution[];
