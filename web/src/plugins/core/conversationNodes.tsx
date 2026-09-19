import { DelegationChatLink } from "../../chat/components/DelegationChatLink";
import { reasoningDisclosureChoice } from "../../chat/reasoningDisclosure";
import { For, Show, createMemo, createSignal } from "solid-js";
import type { JSX } from "solid-js";
import { AgentAvatar } from "../../agents/AgentAvatar";
import { useAgentDirectory } from "../../agents/AgentDirectory";
import { fallbackAvatar } from "../../agents/color";
import { HamesApiError, answerQuestion, inspectContext, resolveApproval } from "../../api/client";
import type { ContextInspection } from "../../api/types";
import type { ConversationNode, PromptSource } from "../../chat/projection";
import { Button } from "../../components/Button";
import { Checkbox } from "../../components/Checkbox";
import { DialogFrame } from "../../components/DialogFrame";
import { TextField } from "../../components/FormField";
import { Lightbox } from "../../components/Lightbox";
import type { LightboxItem } from "../../components/Lightbox";
import {
  ConversationDisclosure,
  type ConversationDisclosureState,
} from "../../components/ConversationDisclosure";
import { Markdown, MarkdownInline } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { TextArea } from "../../components/TextArea";
import { DiffCard, TerminalCard } from "../../chat/components/ToolCards";
import {
  diffPresentation,
  taskCompactSummary,
  terminalPresentation,
  toolCompactSummary,
  toolTitle,
} from "../../chat/toolPresentation";
import { Icon } from "../../shell/icons";
import type { ConversationNodeContribution } from "../../shell/plugins";

function toolState(status: string): ConversationDisclosureState {
  if (["requested", "started", "running", "pending"].includes(status)) return "running";
  if (["failed", "error"].includes(status)) return "error";
  if (["rejected", "cancelled", "stopped"].includes(status)) return "warning";
  if (["completed", "complete", "ok", "success"].includes(status)) return "success";
  return "idle";
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

function TaskToolNode(props: { node: Extract<ConversationNode, { kind: "tool" }> }): JSX.Element {
  const state = () => toolState(props.node.status);
  return (
    <article class="task-tool-row" data-state={state()}>
      <span class="disclosure-leading">
        <Icon name="conversation.tasks" size={15} />
      </span>
      <span class="disclosure-title">
        {props.node.name === "task_list"
          ? "Checked tasks"
          : state() === "running" ? "Updating tasks" : "Updated tasks"}
      </span>
      <span class="disclosure-separator" aria-hidden="true" />
      <span class="disclosure-summary">{taskCompactSummary(props.node)}</span>
      <Show when={toolStatusLabel(props.node.status, state())}>
        {(label) => <span class="disclosure-status">{label()}</span>}
      </Show>
    </article>
  );
}

function formatPromptTokens(value: number): string {
  if (value < 1_000) return value.toLocaleString();
  if (value < 1_000_000) return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(value < 10_000_000 ? 1 : 0)}m`;
}

function promptSourceLabel(source: PromptSource): string {
  if (source.skillSlug) return source.skillSlug;
  if (source.memoryId) return source.memoryLayer ? `${source.memoryLayer} memory` : source.memoryId;
  if (source.path) return source.path;
  return source.id || source.type || "Context source";
}

function ContextSourceList(props: { label: string; sources: readonly PromptSource[]; omitted?: boolean }) {
  return (
    <Show when={props.sources.length > 0}>
      <section class="prompt-injection-section">
        <span class="prompt-injection-label">{props.label}</span>
        <ul class="prompt-source-list">
          <For each={props.sources}>{(source) => (
            <li>
              <div>
                <strong title={promptSourceLabel(source)}>{promptSourceLabel(source)}</strong>
                <span>{source.type || "context"}</span>
              </div>
              <span>
                {formatPromptTokens(props.omitted ? source.estimatedTokens : source.tokens)} tokens
                <Show when={props.omitted && source.reason}> · {source.reason}</Show>
              </span>
            </li>
          )}</For>
        </ul>
      </section>
    </Show>
  );
}

function ContextNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "context") return <></>;
  const node = props.node;
  const [inspection, setInspection] = createSignal<ContextInspection>();
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const injectedTokens = () => node.selectedSources.reduce((sum, source) => sum + source.tokens, 0);
  const systemPrompt = () => {
    const value = inspection()?.request_snapshot.system;
    if (typeof value !== "string") return "";
    return value.length > 20_000
      ? `${value.slice(0, 20_000)}\n\n… ${value.length.toLocaleString()} characters total`
      : value;
  };
  const toolCount = () => {
    const tools = inspection()?.request_snapshot.tools;
    return Array.isArray(tools) ? tools.length : 0;
  };
  const load = async () => {
    if (inspection() || loading()) return;
    setLoading(true);
    setError("");
    try {
      setInspection(await inspectContext(node.eventId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Hames could not inspect this prompt.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <ConversationDisclosure
      class="prompt-injection-node"
      icon="conversation.context"
      title="Prompt context"
      summary={(
        <span>
          {node.selectedSources.length} {node.selectedSources.length === 1 ? "source" : "sources"}
          {` · ${formatPromptTokens(injectedTokens())} source tokens · ${formatPromptTokens(node.estimatedTokens)} request`}
        </span>
      )}
      onToggle={(event) => {
        if (event.currentTarget.open) void load();
      }}
    >
      <div class="prompt-injection-meta">
        <span>{node.provider} / {node.model}</span>
        <Show when={inspection()}>
          <span>{toolCount()} {toolCount() === 1 ? "tool" : "tools"} exposed</span>
        </Show>
      </div>
      <p class="tool-detail-empty">Shown when context changes. Every request snapshot is available in Events.</p>
      <ContextSourceList label="Selected sources" sources={node.selectedSources} />
      <ContextSourceList label="Omitted sources" sources={node.omittedSources} omitted />
      <Show when={loading()}>
        <div class="prompt-injection-loading"><Spinner size="sm" label="Loading model-facing prompt" /> Loading exact prompt…</div>
      </Show>
      <Show when={error()}>
        <div class="prompt-injection-error" role="alert">
          <span>{error()}</span>
          <Button variant="ghost" size="small" onClick={() => void load()}>Try again</Button>
        </div>
      </Show>
      <Show when={inspection() && systemPrompt()}>
        <section class="prompt-injection-section prompt-system-section">
          <span class="prompt-injection-label">Model-facing system prompt</span>
          <pre>{systemPrompt()}</pre>
        </section>
      </Show>
    </ConversationDisclosure>
  );
}

function CompactionNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "compaction") return <></>;
  const node = props.node;
  const state = (): ConversationDisclosureState => {
    if (node.status === "running") return "running";
    if (node.status === "completed") return "success";
    if (node.status === "failed") return "error";
    return "warning";
  };
  const title = () => {
    if (node.status === "running") return "Compacting context";
    if (node.status === "failed") return "Context compaction failed";
    if (node.status === "cancelled") return "Context compaction cancelled";
    return "Compacted context";
  };
  const compacted = () => node.turnsCompacted > 0
    ? `${node.turnsCompacted} ${node.turnsCompacted === 1 ? "turn" : "turns"} · ${formatPromptTokens(node.beforeTokens)} → ${formatPromptTokens(node.afterTokens)} tokens`
    : node.message || "Summarizing older conversation…";
  const statusLabel = () => {
    if (node.status === "running") return "Running";
    if (node.status === "failed") return "Failed";
    if (node.status === "cancelled") return "Cancelled";
    return node.partial ? "Partial" : undefined;
  };

  return (
    <ConversationDisclosure
      class="compaction-node"
      icon="conversation.context"
      title={title()}
      summary={compacted()}
      state={state()}
      statusLabel={statusLabel()}
      open={node.status === "running"}
    >
      <Show when={node.summary} fallback={<p>{node.message || "Hames is condensing older turns into durable context."}</p>}>
        <Markdown content={node.summary} class="message-copy" />
      </Show>
    </ConversationDisclosure>
  );
}

function MaintenanceNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "maintenance") return <></>;
  const node = props.node;
  const state = (): ConversationDisclosureState => {
    if (node.phase === "running") return "running";
    if (node.phase === "completed") return "success";
    if (node.phase === "failed") return "error";
    if (node.phase === "paused") return "warning";
    return "idle";
  };
  const status = () => {
    if (node.phase === "running") return "Working";
    if (node.phase === "queued") return "Queued";
    if (node.phase === "paused") return "Paused";
    if (node.phase === "failed") return "Failed";
    return undefined;
  };

  return (
    <article
      class="maintenance-node"
      data-state={state()}
      data-kind={node.category}
      aria-label={`${node.category === "dream" ? "Dream" : "Wrap-up"}: ${node.detail}`}
    >
      <span class="disclosure-leading">
        <Icon
          name={node.category === "dream" ? "conversation.dream" : "conversation.wrapUp"}
          size={15}
        />
      </span>
      <span class="disclosure-title">{node.category === "dream" ? "Dream" : "Wrap-up"}</span>
      <span class="disclosure-separator" aria-hidden="true" />
      <span class="disclosure-summary">{node.detail}</span>
      <Show when={status()}>{(label) => <span class="disclosure-status">{label()}</span>}</Show>
    </article>
  );
}

function ToolNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "tool") return <></>;
  const node = props.node;
  if (["task_list", "task_update"].includes(node.name)) return <TaskToolNode node={node} />;
  const state = () => toolState(node.status);
  const argumentsText = () => node.arguments ? JSON.stringify(node.arguments, null, 2) : "";
  const terminal = createMemo(() => terminalPresentation(node));
  const diff = createMemo(() => diffPresentation(node));
  const specialized = () => Boolean(terminal() || diff());

  return (
    <ConversationDisclosure
      class="tool-node"
      icon="conversation.tool"
      title={toolTitle(node.name)}
      summary={diff()
        ? (
          <span class="tool-diff-summary">
            <span class="tool-diff-path">{diff()!.path}</span>
            <span aria-hidden="true">·</span>
            <span class="diff-added">+{diff()!.added}</span>
            <span class="diff-removed">−{diff()!.removed}</span>
          </span>
        )
        : <MarkdownInline content={toolCompactSummary(node)} />}
      state={state()}
      statusLabel={toolStatusLabel(node.status, state())}
    >
      <Show when={terminal()} keyed>
        {(presentation) => <TerminalCard terminal={presentation} />}
      </Show>
      <Show when={diff()} keyed>
        {(presentation) => (
          <DiffCard diff={presentation} content={node.content ?? ""} truncated={node.truncated} />
        )}
      </Show>
      <Show when={!specialized()}>
        <Show when={argumentsText()}>
          {(content) => <ToolDetailSection label="Input" content={content()} />}
        </Show>
        <Show when={node.content}>
          {(content) => <ToolDetailSection label="Output" content={content()} />}
        </Show>
        <Show when={!argumentsText() && !node.content}>
          <p class="tool-detail-empty">No additional details were recorded.</p>
        </Show>
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
  const [choice, setChoice] = reasoningDisclosureChoice(node.id);
  const open = () => choice() ?? false;
  return (
    <ConversationDisclosure
      class="reasoning-node"
      icon="conversation.reasoning"
      title={node.live ? "Thinking" : "Reasoning"}
      summary={<MarkdownInline content={reasoningSummary(node.content, Boolean(node.live))} />}
      state={node.live ? "running" : "idle"}
      statusLabel={node.live ? "Running" : undefined}
      open={open()}
      onToggle={(event) => {
        const next = event.currentTarget.open;
        if (next !== open()) setChoice(next);
      }}
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
  const [previewing, setPreviewing] = createSignal<LightboxItem>();
  const [copyLabel, setCopyLabel] = createSignal("Copy");
  const copyMessage = async () => {
    try {
      await navigator.clipboard.writeText(node.content);
      setCopyLabel("Copied");
    } catch {
      setCopyLabel("Copy failed");
    }
  };

  return (
    <article class={`message-node ${node.kind}`}>
      <MessageAvatar
        kind={messageKind}
        agentName={agentName()}
        avatar={agent()?.avatar ?? fallbackAvatar(agentId())}
      />
      <div class="message-content">
        <div class="message-role"><span>{node.kind === "user" ? "You" : agentName()}</span>
          <Show when={node.kind === "assistant" && !node.live}>
            <button class="message-copy-button" type="button" aria-label="Copy message" title="Copy full message as Markdown" onClick={() => void copyMessage()}>{copyLabel()}</button>
          </Show>
        </div>
        <Show when={node.kind === "user" && node.attachments?.length}>
          <div class="message-attachments">
            {node.attachments?.map((attachment) => {
              const source = `/v1/sessions/${encodeURIComponent(node.sessionId ?? "")}/attachments/${encodeURIComponent(attachment.digest)}`;
              return attachment.kind === "image" ? (
                <button
                  type="button"
                  class="message-attachment-preview image"
                  title={attachment.name}
                  aria-label={`Preview ${attachment.name}`}
                  onClick={() => setPreviewing({
                    name: attachment.name,
                    kind: "image",
                    mediaType: attachment.media_type,
                    size: attachment.size,
                    source,
                  })}
                >
                  <img src={source} alt={attachment.name} />
                </button>
              ) : (
                <button
                  type="button"
                  class="message-attachment-preview message-file-attachment"
                  aria-label={`Preview ${attachment.name}`}
                  onClick={() => setPreviewing({
                    name: attachment.name,
                    kind: "text",
                    mediaType: attachment.media_type,
                    size: attachment.size,
                    source,
                  })}
                >
                  <Icon name="action.file" size={18} />
                  <span>{attachment.name}</span>
                </button>
              );
            })}
          </div>
        </Show>
        <Markdown content={node.content} class="message-copy" live={node.live} />
      </div>
      <Show when={previewing()} keyed>
        {(item) => <Lightbox item={item} onClose={() => setPreviewing(undefined)} />}
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
    <Show when={node.status === "pending"} fallback={
      <section class="decision-card approval-card resolved" aria-label={`Approval for ${node.name}`}>
        <div class="decision-heading">
          <div>
            <span class="eyebrow">Permission decision</span>
            <h2>{node.name}</h2>
          </div>
          <span class="decision-status">{node.status}</span>
        </div>
        <Show when={node.reason}><p>{node.reason}</p></Show>
      </section>
    }>
      <DialogFrame
        eyebrow="Permission request"
        title={`Allow ${node.name}?`}
        class="approval-dialog"
        closeLabel="Deny permission request"
        onClose={() => void decide("denied")}
        footer={(
          <>
            <Show when={error()}><span class="dialog-error" role="alert">{error()}</span></Show>
            <Button variant="destructive" disabled={busy()} onClick={() => void decide("denied")}>
              Deny
            </Button>
            <Show when={node.allowSession}>
              <Button
                class="approval-allow"
                disabled={busy()}
                onClick={() => void decide("approved_session")}
              >
                Allow for session
              </Button>
            </Show>
            <Button
              class="approval-allow"
              variant="primary"
              loading={busy()}
              onClick={() => void decide("approved")}
            >
              Allow once
            </Button>
          </>
        )}
      >
        <div class="approval-dialog-body">
          <Show when={node.reason} fallback={<p>Hames needs permission to continue this action.</p>}>
            <p>{node.reason}</p>
          </Show>
          <details>
            <summary>Request details</summary>
            <pre>{JSON.stringify(node.arguments, null, 2)}</pre>
          </details>
        </div>
      </DialogFrame>
    </Show>
  );
}

function QuestionNode(props: { node: ConversationNode }): JSX.Element {
  if (props.node.kind !== "question") return <></>;
  const node = props.node;
  const [note, setNote] = createSignal("");
  const [customAnswer, setCustomAnswer] = createSignal("");
  const [selectedOptions, setSelectedOptions] = createSignal<string[]>([]);
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");

  const choose = async (selectedOption: string) => {
    if (busy() || node.status !== "pending") return;
    setBusy(true);
    setError("");
    try {
      await answerQuestion(node.questionId, {
        selected_option: selectedOption,
        selected_options: [],
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
        selected_options: [],
        note: "",
        custom_answer: answer,
      });
    } catch (caught) {
      setError(mutationError(caught));
    } finally {
      setBusy(false);
    }
  };

  const toggleOption = (label: string, checked: boolean) => {
    setSelectedOptions((current) => checked
      ? current.includes(label) ? current : [...current, label]
      : current.filter((candidate) => candidate !== label)
    );
  };

  const submitMultiple = async () => {
    const selected = selectedOptions();
    if (
      busy() ||
      node.status !== "pending" ||
      selected.length < node.minSelections ||
      selected.length > node.maxSelections
    ) return;
    setBusy(true);
    setError("");
    try {
      await answerQuestion(node.questionId, {
        selected_option: null,
        selected_options: selected,
        note: note().trim(),
        custom_answer: "",
      });
    } catch (caught) {
      setError(mutationError(caught));
    } finally {
      setBusy(false);
    }
  };

  const selectionHint = () => {
    if (node.minSelections === node.maxSelections) {
      return `Choose ${node.minSelections}`;
    }
    return `Choose ${node.minSelections}–${node.maxSelections}`;
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
        <Show when={node.answerType === "single_choice"} fallback={
          <Show when={node.answerType === "multiple_choice"} fallback={
            <div class="question-text-answer">
              <TextField
                label="Your answer"
                value={customAnswer()}
                placeholder={node.placeholder || "Type your answer"}
                autocomplete="off"
                autofocus
                onInput={(event) => setCustomAnswer(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.isComposing) {
                    event.preventDefault();
                    void submitCustom();
                  }
                }}
              />
              <Button
                variant="primary"
                disabled={!customAnswer().trim() || busy()}
                onClick={() => void submitCustom()}
              >
                Send answer
              </Button>
            </div>
          }>
            <fieldset class="question-multiple-options" disabled={busy()}>
              <legend class="visually-hidden">Available answers</legend>
              <div class="question-selection-summary" aria-live="polite">
                <span>{selectionHint()}</span>
                <span>{selectedOptions().length} selected</span>
              </div>
              <For each={node.options}>
                {(option) => {
                  const checked = () => selectedOptions().includes(option.label);
                  return (
                    <Checkbox
                      class="question-checkbox-option"
                      checked={checked()}
                      disabled={busy() || (!checked() && selectedOptions().length >= node.maxSelections)}
                      label={<strong>{option.label}</strong>}
                      description={option.description}
                      onCheckedChange={(next) => toggleOption(option.label, next)}
                    />
                  );
                }}
              </For>
            </fieldset>
            <TextArea
              class="decision-input"
              value={note()}
              resize="none"
              placeholder="Optional note about your selections"
              aria-label="Selection note"
              onInput={(event) => setNote(event.currentTarget.value)}
            />
            <div class="question-submit-row">
              <Button
                variant="primary"
                disabled={
                  busy() ||
                  selectedOptions().length < node.minSelections ||
                  selectedOptions().length > node.maxSelections
                }
                onClick={() => void submitMultiple()}
              >
                Submit choices
              </Button>
            </div>
          </Show>
        }>
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
          <TextArea
            class="decision-input"
            value={note()}
            resize="none"
            placeholder="Optional note with your choice"
            aria-label="Option note"
            onInput={(event) => setNote(event.currentTarget.value)}
          />
          <div class="custom-answer">
            <TextArea
              class="decision-input"
              value={customAnswer()}
              resize="vertical"
              resizeLabel="Resize custom answer"
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
      </Show>
      <Show when={error()}>
        <p class="decision-error" role="alert">{error()}</p>
      </Show>
    </section>
  );
}

function DelegationNodeView(props: { node: ConversationNode }) {
  const node = () => props.node as Extract<ConversationNode, { kind: "delegation" }>;
  return <div class="delegation-card"><div class="delegation-handoff" role="status" data-state={node().status}>
    <span class="delegation-handoff-icon"><Show when={node().status === "working"} fallback={node().status === "completed" ? "✓" : "!"}><Spinner size="sm" /></Show></span>
    <div><span>{node().agentId} · {node().status === "completed" ? "Finished" : node().status === "working" ? "Working" : node().status === "stopping" ? "Stopping" : node().status === "cancelled" ? "Cancelled" : node().status}</span>
      <Show when={node().model}><small>{node().model}{node().effort ? ` · ${node().effort}` : ""}</small></Show>
    </div>
  </div><Show when={node().parentSessionId}><DelegationChatLink node={node()} /></Show></div>;
}

export const coreConversationNodes = [
  { kind: "delegation", component: DelegationNodeView },
  { kind: "user", component: MessageNode },
  { kind: "context", component: ContextNode },
  { kind: "compaction", component: CompactionNode },
  { kind: "maintenance", component: MaintenanceNode },
  { kind: "assistant", component: MessageNode },
  { kind: "reasoning", component: ReasoningNode },
  { kind: "tool", component: ToolNode },
  { kind: "notice", component: NoticeNode },
  { kind: "approval", component: ApprovalNode },
  { kind: "question", component: QuestionNode },
] satisfies readonly ConversationNodeContribution[];
