import { For, Show, createMemo, createSignal } from "solid-js";
import type { JSX } from "solid-js";
import { AgentAvatar } from "../../agents/AgentAvatar";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import { fallbackAvatar } from "../../agents/color";
import { HamesApiError, answerQuestion, resolveApproval } from "../../api/client";
import type { ConversationNode } from "../../chat/projection";
import { Button } from "../../components/Button";
import {
  ConversationDisclosure,
  type ConversationDisclosureState,
} from "../../components/ConversationDisclosure";
import { Markdown, MarkdownInline } from "../../components/Markdown";
import { Icon } from "../../shell/icons";
import type { ConversationNodeContribution } from "../../shell/plugins";

function toolState(status: string): ConversationDisclosureState {
  if (["requested", "started", "running", "pending"].includes(status)) return "running";
  if (["failed", "error"].includes(status)) return "error";
  if (["rejected", "cancelled", "stopped"].includes(status)) return "warning";
  if (["completed", "complete", "ok", "success"].includes(status)) return "success";
  return "idle";
}

function toolSummary(node: Extract<ConversationNode, { kind: "tool" }>): string {
  if (node.summary) return node.summary;
  switch (toolState(node.status)) {
    case "running": return "Working…";
    case "error": return "The call failed";
    case "warning": return "The call stopped";
    case "success": return "Completed";
    default: return "Details";
  }
}

function toolStatusLabel(status: string, state: ConversationDisclosureState): string | undefined {
  if (state === "running") return "Running";
  if (state === "error") return "Failed";
  if (state === "warning") return status;
  return undefined;
}

function ToolDetailSection(props: { label: string; content: string }): JSX.Element {
  return (
    <section class="tool-detail-section">
      <span>{props.label}</span>
      <pre>{props.content}</pre>
    </section>
  );
}

function ToolNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "tool") return <></>;
  const node = props.node;
  const state = () => toolState(node.status);
  const argumentsText = () => node.arguments ? JSON.stringify(node.arguments, null, 2) : "";

  return (
    <ConversationDisclosure
      class="tool-node"
      icon="conversation.tool"
      title={node.name}
      summary={<MarkdownInline content={toolSummary(node)} />}
      state={state()}
      statusLabel={toolStatusLabel(node.status, state())}
    >
      <Show when={argumentsText()}>
        {(content) => <ToolDetailSection label="Input" content={content()} />}
      </Show>
      <Show when={node.content}>
        {(content) => <ToolDetailSection label="Output" content={content()} />}
      </Show>
      <Show when={!argumentsText() && !node.content}>
        <p class="tool-detail-empty">No additional details were recorded.</p>
      </Show>
    </ConversationDisclosure>
  );
}

function reasoningSummary(content: string, live: boolean): string {
  const lines = content.trim().split("\n").map((line) => line.trim()).filter(Boolean);
  return (live ? lines.at(-1) : lines[0]) ?? (live ? "Thinking…" : "Reasoning");
}

function ReasoningNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "reasoning") return <></>;
  const node = props.node;
  return (
    <ConversationDisclosure
      class="reasoning-node"
      icon="conversation.reasoning"
      title={node.live ? "Thinking" : "Reasoning"}
      summary={<MarkdownInline content={reasoningSummary(node.content, Boolean(node.live))} />}
      state={node.live ? "running" : "idle"}
      statusLabel={node.live ? "Running" : undefined}
      open={Boolean(node.live)}
    >
      <Markdown content={node.content} class="message-copy" live={node.live} />
    </ConversationDisclosure>
  );
}

function NoticeNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "notice") return <></>;
  return <p class={`conversation-notice ${props.node.tone}`}>{props.node.content}</p>;
}

function MessageAvatar(props: {
  kind: "user" | "assistant";
  agentName: string;
  avatar: ReturnType<typeof fallbackAvatar>;
}): JSX.Element {
  return (
    <div class="message-avatar" aria-hidden="true">
      <Show
        when={props.kind === "assistant"}
        fallback={<span class="user-avatar"><Icon name="message.user" size={15} /></span>}
      >
        <AgentAvatar
          config={props.avatar}
          name={props.agentName}
          size={30}
        />
      </Show>
    </div>
  );
}

function MessageNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "user" && props.node.kind !== "assistant") return <></>;
  const node = props.node;
  const directory = useAgentDirectory();
  const agent = createMemo(() =>
    node.kind === "assistant"
      ? directory.agents().find((candidate) => candidate.id === node.agentId)
      : undefined,
  );
  const agentId = () => node.agentId || "default";
  const agentName = () => agent()?.name ?? (agentId() === "default" ? "Hames" : agentId());
  const messageKind = node.kind === "user" ? "user" : "assistant";

  return (
    <article class={`message-node ${node.kind}`}>
      <MessageAvatar
        kind={messageKind}
        agentName={agentName()}
        avatar={agent()?.avatar ?? fallbackAvatar(agentId())}
      />
      <div class="message-content">
        <div class="message-role">{node.kind === "user" ? "You" : agentName()}</div>
        <Markdown content={node.content} class="message-copy" live={node.live} />
      </div>
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
          <Button variant="quiet" disabled={busy()} onClick={() => void decide("denied")}>
            Deny
          </Button>
          <Show when={node.allowSession}>
            <Button disabled={busy()} onClick={() => void decide("approved_session")}>
              Allow for session
            </Button>
          </Show>
          <Button variant="primary" disabled={busy()} onClick={() => void decide("approved")}>
            Allow once
          </Button>
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
              <Button variant="choice" disabled={busy()} onClick={() => void choose(option.label)}>
                <strong>{option.label}</strong>
                <Show when={option.description}><span>{option.description}</span></Show>
              </Button>
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
          <Button
            variant="primary"
            disabled={!customAnswer().trim() || busy()}
            onClick={() => void submitCustom()}
          >
            Send answer
          </Button>
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
