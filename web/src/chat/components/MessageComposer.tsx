import { useNavigate } from "@solidjs/router";
import { Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import type { JSX } from "solid-js";
import {
  HamesApiError,
  cancelRun,
  listAvailableSkills,
  listUserCommands,
  probeProvider,
  sendMessage,
  sendPlanFeedback,
  executePlan,
  trustSession,
} from "../../api/client";
import type { MessageAttachmentUpload, Session, SkillCatalogEntry } from "../../api/types";
import { Button } from "../../components/Button";
import { Lightbox } from "../../components/Lightbox";
import type { LightboxItem } from "../../components/Lightbox";
import { TextArea } from "../../components/TextArea";
import { Icon } from "../../shell/icons";
import { useWorkspace } from "../../shell/workspace";
import { coreSlashCommands, skillSlashCommands } from "../slashCommands";
import type { SlashCommand } from "../slashCommands";
import { movedCommandMessage, parseWebCommand } from "../webCommands";
import { commandWorkTitle, executeWebCommand } from "../commandExecution";
import { PlanReview } from "./PlanReview";
import type { ReviewPlan } from "../planReview";
import { MessageQueue } from "./MessageQueue";
import { ComposerSeat } from "./ComposerSeat";
import { ComposerActionMenu } from "./ComposerActionMenu";
import { ComposerStatsLine } from "./ComposerStatsLine";
import { SlashCommandMenu } from "./SlashCommandMenu";

interface MessageComposerProps {
  session: Session;
  activeRunId?: string;
  queueRevision?: string;
  hidden?: boolean;
  onSessionChanged: () => void;
  onCommandResult?: (message: string) => void;
  onSessionUpdated: (session: Session) => void;
  onSessionOpened: (session: Session) => void;
  workspaceControl?: JSX.Element;
  taskCard?: JSX.Element;
  plan?: ReviewPlan;
  showStats?: boolean;
}

function errorMessage(error: unknown): string {
  if (error instanceof HamesApiError) return error.message;
  return error instanceof Error ? error.message : "Hames could not complete the request.";
}

interface DraftAttachment {
  file: File;
  previewUrl?: string;
}

function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Unable to read ${file.name}`));
    reader.onload = () => {
      const value = String(reader.result ?? "");
      resolve(value.slice(value.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

function fileDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Unable to preview ${file.name}`));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsDataURL(file);
  });
}

function fileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Unable to preview ${file.name}`));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsText(file);
  });
}

function fileTypeLabel(name: string): string {
  const extension = name.split(".").at(-1)?.trim();
  if (!extension || extension === name || extension.length > 5) return "FILE";
  return extension.toLocaleUpperCase();
}

function releasePreview(url?: string): void {
  if (url?.startsWith("blob:")) URL.revokeObjectURL(url);
}

function draftStorageKey(sessionId: string): string {
  return `hames.composer-draft:${sessionId}`;
}

function readStoredDraft(sessionId: string): string {
  try {
    return window.localStorage.getItem(draftStorageKey(sessionId)) ?? "";
  } catch {
    return "";
  }
}

function storeDraft(sessionId: string, value: string): void {
  try {
    if (value) window.localStorage.setItem(draftStorageKey(sessionId), value);
    else window.localStorage.removeItem(draftStorageKey(sessionId));
  } catch {
    // Draft persistence is a convenience; a blocked storage API must not disable the composer.
  }
}

export function provisionalSessionTitle(content: string, attachmentName = ""): string {
  const normalized = (content.trim() || attachmentName.trim() || "New conversation")
    .replace(/\s+/g, " ");
  if (normalized.length <= 72) return normalized;
  const clipped = normalized.slice(0, 71);
  const wordBoundary = clipped.lastIndexOf(" ");
  return `${clipped.slice(0, wordBoundary >= 40 ? wordBoundary : 71).trimEnd()}…`;
}

export function MessageComposer(props: MessageComposerProps) {
  const navigate = useNavigate();
  const workspace = useWorkspace();
  let draftSessionId = props.session.id;
  let submissionGeneration = 0;
  let disposed = false;
  onCleanup(() => { disposed = true; });
  const [draft, setDraft] = createSignal(readStoredDraft(draftSessionId));
  const [sending, setSending] = createSignal(false);
  const [executingPlan, setExecutingPlan] = createSignal(false);
  const [executionSubmitted, setExecutionSubmitted] = createSignal("");
  const [feedbackPlan, setFeedbackPlan] = createSignal("");
  const [feedbackSubmitted, setFeedbackSubmitted] = createSignal("");
  const reviewingPlan = () => props.plan && props.session.interaction_mode === "plan"
    && ["ready", "reviewing"].includes(props.plan.status);
  const resumablePlan = () => props.plan && ["failed", "needs_attention"].includes(props.plan.status);
  const visiblePlan = createMemo(() => {
    const plan = props.plan;
    if (!plan || !reviewingPlan() || props.activeRunId || plan.status !== "ready"
      || plan.id === executionSubmitted() || plan.id === feedbackSubmitted()) return undefined;
    return plan;
  });
  const approvePlan = async () => {
    if (sending() || executingPlan() || props.activeRunId || (!reviewingPlan() && !resumablePlan()) || draft().trim() || attachments().length) return;
    const source = props.session;
    const planId = props.plan!.id;
    setExecutingPlan(true);
    setComposerError("");
    try {
      await executePlan(source.id);
      if (!disposed && props.session.id === source.id) {
        setExecutionSubmitted(planId);
        setFeedbackPlan("");
        props.onSessionUpdated({ ...source, interaction_mode: "auto" });
      }
      props.onSessionChanged();
    } catch (error) {
      if (!disposed && props.session.id === source.id) setComposerError(errorMessage(error));
    } finally {
      if (!disposed && props.session.id === source.id) setExecutingPlan(false);
    }
  };
  const [cancelling, setCancelling] = createSignal(false);
  const [composerError, setComposerError] = createSignal("");
  const [trustRequired, setTrustRequired] = createSignal(false);
  const [trusting, setTrusting] = createSignal(false);
  const [submissionNote, setSubmissionNote] = createSignal("");
  const [requestedCommands, setRequestedCommands] = createSignal(false);
  const [actionsOpen, setActionsOpen] = createSignal(false);
  const [attachments, setAttachments] = createSignal<DraftAttachment[]>([]);
  const [attachmentsCollapsed, setAttachmentsCollapsed] = createSignal(false);
  const [previewing, setPreviewing] = createSignal<LightboxItem>();
  const [dismissedSlashDraft, setDismissedSlashDraft] = createSignal("");
  const [selectedCommandIndex, setSelectedCommandIndex] = createSignal(0);
  const [customCommands, setCustomCommands] = createSignal<import("../../api/client").UserCommand[]>([]);
  const [availableSkills, setAvailableSkills] = createSignal<SkillCatalogEntry[]>([]);
  let textarea!: HTMLTextAreaElement;
  let composerShell!: HTMLDivElement;
  let attachmentInput!: HTMLInputElement;
  let loadedSkillCommandsFor = "";
  let loadingSkillCommandsFor = "";

  const slashQuery = createMemo<string | null>(() => {
    const match = /^\s*\/([^\s/]*)$/.exec(draft());
    return match ? (match[1] ?? "").toLocaleLowerCase() : null;
  });
  const commands = createMemo(() => [
    ...coreSlashCommands,
    ...customCommands().map(command => ({ value: `/${command.name}`, detail: command.description, argumentHint: "[execution note]" })),
    ...skillSlashCommands(availableSkills()).filter(skill => !customCommands().some(command => skill.value === `/${command.name}`)),
  ]);
  const visibleCommands = createMemo(() => {
    const query = requestedCommands() ? "" : slashQuery();
    if (query === null || !query) return commands();
    return commands().filter((command) =>
      command.value.slice(1).toLocaleLowerCase().includes(query) ||
      command.detail.toLocaleLowerCase().includes(query)
    );
  });
  const commandMenuOpen = createMemo(() =>
    requestedCommands() ||
    (slashQuery() !== null && dismissedSlashDraft() !== draft())
  );

  const loadSkillCommands = async () => {
    const sessionId = props.session.id;
    if (loadedSkillCommandsFor === sessionId || loadingSkillCommandsFor === sessionId) return;
    loadingSkillCommandsFor = sessionId;
    try {
      const [catalog, custom] = await Promise.all([
        listAvailableSkills(sessionId), listUserCommands(sessionId).catch(() => []),
      ]);
      if (props.session.id !== sessionId) return;
      setAvailableSkills(catalog);
      setCustomCommands(custom);
      loadedSkillCommandsFor = sessionId;
    } catch {
      // Core commands remain usable while the Skill catalog is unavailable.
    } finally {
      if (loadingSkillCommandsFor === sessionId) loadingSkillCommandsFor = "";
    }
  };

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
    const sessionId = props.session.id;
    if (sessionId === draftSessionId) return;
    draftSessionId = sessionId;
    setCustomCommands([]);
    ++submissionGeneration;
    setExecutingPlan(false);
    setExecutionSubmitted("");
    setFeedbackPlan("");
    setFeedbackSubmitted("");
    setSending(false);
    setComposerError("");
    setSubmissionNote("");
    setDraft(readStoredDraft(sessionId));
    setDismissedSlashDraft("");
  });

  createEffect(() => {
    storeDraft(draftSessionId, draft());
  });

  createEffect(() => {
    draft();
    queueMicrotask(resizeTextarea);
  });

  createEffect(() => {
    props.session.id;
    setAvailableSkills([]);
    loadedSkillCommandsFor = "";
    loadingSkillCommandsFor = "";
  });

  createEffect(() => {
    if (commandMenuOpen()) void loadSkillCommands();
  });

  createEffect(() => {
    slashQuery();
    visibleCommands().map((command) => command.value).join("\0");
    setSelectedCommandIndex(0);
  });

  createEffect(() => {
    if (!commandMenuOpen() && !actionsOpen()) return;
    const closeOutside = (event: MouseEvent) => {
      if (composerShell && !composerShell.contains(event.target as Node)) {
        setRequestedCommands(false);
        setActionsOpen(false);
        setDismissedSlashDraft(draft());
      }
    };
    document.addEventListener("mousedown", closeOutside);
    onCleanup(() => document.removeEventListener("mousedown", closeOutside));
  });

  onCleanup(() => {
    for (const attachment of attachments()) {
      releasePreview(attachment.previewUrl);
    }
  });

  const clearAttachments = () => {
    for (const attachment of attachments()) {
      releasePreview(attachment.previewUrl);
    }
    setAttachments([]);
    setAttachmentsCollapsed(false);
  };

  const removeAttachment = (selected: DraftAttachment) => {
    releasePreview(selected.previewUrl);
    setAttachments((current) => {
      const next = current.filter((attachment) => attachment !== selected);
      if (next.length === 0) setAttachmentsCollapsed(false);
      return next;
    });
  };

  const previewAttachment = async (attachment: DraftAttachment) => {
    try {
      if (attachment.file.type.startsWith("image/")) {
        setPreviewing({
          name: attachment.file.name,
          kind: "image",
          mediaType: attachment.file.type,
          size: attachment.file.size,
          source: attachment.previewUrl,
        });
        return;
      }
      setPreviewing({
        name: attachment.file.name,
        kind: "text",
        mediaType: attachment.file.type || "text/plain",
        size: attachment.file.size,
        content: await fileText(attachment.file),
      });
    } catch (error) {
      setComposerError(errorMessage(error));
    }
  };

  const selectAttachments = async (files: FileList | null) => {
    if (!files?.length) return;
    const selected = [...files];
    attachmentInput.value = "";
    if (attachments().length + selected.length > 8) {
      setComposerError("A message can contain at most 8 attachments.");
      return;
    }
    if (selected.some((file) => file.type.startsWith("image/"))) {
      try {
        const probe = await probeProvider(props.session.provider);
        const model = probe.models.find((candidate) => candidate.id === props.session.model);
        if (!model?.input_modalities?.includes("image")) {
          setComposerError("The selected model does not support image input.");
          return;
        }
      } catch (error) {
        setComposerError(errorMessage(error));
        return;
      }
    }
    setComposerError("");
    const admitted = await Promise.all(selected.map(async (file) => ({
      file,
      previewUrl: file.type.startsWith("image/") ? await fileDataUrl(file) : undefined,
    })));
    setAttachmentsCollapsed(false);
    setAttachments((current) => [
      ...current,
      ...admitted,
    ]);
    queueMicrotask(() => textarea.focus());
  };

  const encodedAttachments = async (): Promise<MessageAttachmentUpload[]> => Promise.all(
    attachments().map(async ({ file }) => ({
      name: file.name,
      media_type: file.type || "application/octet-stream",
      data_base64: await fileBase64(file),
    })),
  );

  const submit = async () => {
    const content = draft().trim();
    if (content.toLowerCase() === "/connect") {
      setDraft("");
      navigate("/settings/connections");
      return;
    }
    if ((!content && attachments().length === 0) || sending() || executingPlan()) return;
    if (reviewingPlan() && attachments().length) {
      setComposerError("Plan feedback supports text. Remove attachments before sending changes.");
      return;
    }
    if (attachments().length > 0 && content.startsWith("/")) {
      setComposerError("Attachments can only be sent with a conversation message, not a slash command.");
      return;
    }
    const sourceSession = props.session;
    let command = parseWebCommand(content);
    if (!command && content.trim().startsWith("/")) {
      try {
        const custom = await listUserCommands(sourceSession.id);
        if (disposed || props.session.id !== sourceSession.id) return;
        setCustomCommands(custom);
        command = parseWebCommand(content, custom.map(item => item.name));
      } catch (error) {
        if (disposed || props.session.id !== sourceSession.id) return;
        setComposerError(errorMessage(error)); return;
      }
    }
    const movedMessage = movedCommandMessage(content);
    if (!command && movedMessage) {
      setSubmissionNote("");
      setComposerError(movedMessage);
      return;
    }
    if (!command && /^\s*\/(?:compact|dream|heal|stop)\b/i.test(content)) {
      setSubmissionNote("");
      setComposerError("That command does not accept arguments.");
      return;
    }
    const feedbackFor = !command && reviewingPlan() ? props.plan?.id : undefined;
    const generation = ++submissionGeneration;
    const isCurrent = () => !disposed && props.session.id === sourceSession.id
      && generation === submissionGeneration;
    const title = command ? commandWorkTitle(command)
      : provisionalSessionTitle(content, attachments()[0]?.file.name);
    const provisional = !sourceSession.title?.trim() && title
      ? provisionalSessionTitle(title) : "";
    setSending(true);
    setComposerError("");
    setTrustRequired(false);
    setSubmissionNote("");
    if (provisional) props.onSessionUpdated({ ...sourceSession, title: provisional });
    try {
      const outcome = command
        ? await executeWebCommand(command, sourceSession.id)
        : await (feedbackFor
          ? sendPlanFeedback(sourceSession.id, content)
          : sendMessage(sourceSession.id, content, await encodedAttachments())).then(accepted => ({
          note: accepted.disposition === "queued" ? "Message queued" : "",
          openedSession: undefined,
        }));
      // Clear only the submitted text, never a newer draft or another session's composer.
      if (readStoredDraft(sourceSession.id).trim() === content) storeDraft(sourceSession.id, "");
      if (isCurrent()) {
        if (feedbackFor) setFeedbackSubmitted(feedbackFor);
        if (draft().trim() === content) {
          setDraft("");
          if (!command) clearAttachments();
        }
        setSubmissionNote(String(generation));
        if (command?.kind === "goal" && command.action === "show" && outcome.note) props.onCommandResult?.(outcome.note);
        if (outcome.openedSession) props.onSessionOpened(outcome.openedSession);
      }
      props.onSessionChanged();
    } catch (error) {
      const current = workspace.session(sourceSession.id);
      if (provisional && current?.title === provisional) {
        props.onSessionUpdated({ ...current, title: sourceSession.title });
      }
      if (isCurrent()) {
        setTrustRequired(error instanceof HamesApiError && error.code === "working_directory_untrusted");
        setComposerError(errorMessage(error));
      }
    } finally {
      if (isCurrent()) setSending(false);
    }
  };

  const trustAndRetry = async () => {
    if (trusting() || sending()) return;
    const sourceSession = props.session;
    setTrusting(true);
    setComposerError("");
    try {
      await trustSession(sourceSession.id);
      if (disposed || props.session.id !== sourceSession.id) return;
      setTrustRequired(false);
      await submit();
    } catch (error) {
      setComposerError(errorMessage(error));
    } finally {
      setTrusting(false);
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

  const openCommands = () => {
    setActionsOpen(false);
    setRequestedCommands(true);
    setDismissedSlashDraft("");
    setSelectedCommandIndex(0);
    textarea.focus();
  };

  const openActions = () => {
    setActionsOpen((open) => !open);
    setRequestedCommands(false);
    setDismissedSlashDraft(draft());
  };

  const insertCommand = (command: SlashCommand) => {
    const current = draft();
    const slash = /^(\s*)\/[^\s/]*$/.exec(current);
    const insertion = `${command.value} `;
    let next: string;
    let cursor: number;
    if (slash) {
      next = `${slash[1]}${insertion}`;
      cursor = next.length;
    } else {
      const start = textarea.selectionStart ?? current.length;
      const end = textarea.selectionEnd ?? start;
      next = `${current.slice(0, start)}${insertion}${current.slice(end)}`;
      cursor = start + insertion.length;
    }
    setDraft(next);
    setRequestedCommands(false);
    setDismissedSlashDraft(next);
    queueMicrotask(() => {
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
    });
  };

  return (
    <div
      class="composer-dock"
      classList={{ hidden: props.hidden }}
      data-chat-region="composer"
      aria-hidden={props.hidden || undefined}
    >
      <Show when={props.workspaceControl}>
        <div class="composer-workspace-control">{props.workspaceControl}</div>
      </Show>
      {props.taskCard}
      <Show when={visiblePlan()}>{(plan) => (
        <PlanReview plan={plan()} canReview={!!reviewingPlan() && !props.activeRunId}
          busy={sending() || executingPlan()} hasDraft={!!draft().trim() || attachments().length > 0}
          revising={feedbackPlan() === plan().id}
          onRevise={() => { setFeedbackPlan(plan().id); textarea.focus(); }}
          onExecute={() => void approvePlan()} />
      )}</Show>
      <div class="composer-trays">
      <Show when={attachments().length > 0}>
        <div
          class="composer-attachment-drawer"
          data-collapsed={attachmentsCollapsed() ? "" : undefined}
          aria-label="Message attachments"
        >
          <button
            type="button"
            class="composer-attachment-drawer-toggle"
            aria-label={attachmentsCollapsed() ? "Expand attachments" : "Collapse attachments"}
            aria-expanded={!attachmentsCollapsed()}
            onClick={() => setAttachmentsCollapsed((collapsed) => !collapsed)}
          >
            <span class="composer-attachment-collapsed-label">
              <span class="composer-attachment-count">{attachments().length}</span>
              <span>Attachments</span>
            </span>
            <span class="composer-attachment-handle" aria-hidden="true" />
          </button>
          <div class="composer-attachment-region" aria-hidden={attachmentsCollapsed() || undefined}>
            <div class="composer-attachments">
              {attachments().map((attachment) => (
                <div class="composer-attachment" data-kind={attachment.previewUrl ? "image" : "file"}>
                  <button
                    type="button"
                    class="composer-attachment-preview"
                    aria-label={`Preview ${attachment.file.name}`}
                    onClick={() => void previewAttachment(attachment)}
                  >
                    <Show when={attachment.previewUrl} fallback={
                      <span class="composer-attachment-file">
                        <Icon name="action.file" size={30} />
                        <small>{fileTypeLabel(attachment.file.name)}</small>
                      </span>
                    }>{(url) => <img src={url()} alt="" />}</Show>
                    <span title={attachment.file.name}>{attachment.file.name}</span>
                  </button>
                  <Button
                    variant="bare"
                    aria-label={`Remove ${attachment.file.name}`}
                    title={`Remove ${attachment.file.name}`}
                    onClick={() => removeAttachment(attachment)}
                  ><Icon name="action.close" size={12} /></Button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </Show>
        <MessageQueue sessionId={props.session.id} revision={`${props.queueRevision ?? ""}:${submissionNote()}`} />
      </div>
      <div class="composer-stack">
        <div class="composer-shell" ref={composerShell}>
          <input
            ref={attachmentInput}
            class="composer-file-input"
            type="file"
            multiple
            hidden
            tabIndex={-1}
            accept="image/png,image/jpeg,image/webp,image/gif,text/*,.c,.cc,.conf,.cpp,.css,.csv,.go,.h,.hpp,.html,.ini,.java,.js,.json,.jsx,.log,.md,.py,.rb,.rs,.sh,.sql,.svg,.toml,.ts,.tsx,.xml,.yaml,.yml"
            aria-label="Upload files or images"
            onChange={(event) => void selectAttachments(event.currentTarget.files)}
          />
          <SlashCommandMenu
            open={commandMenuOpen()}
            commands={visibleCommands()}
            selectedIndex={selectedCommandIndex()}
            onSelectedIndexChange={setSelectedCommandIndex}
            onSelect={insertCommand}
          />
          <TextArea
            elementRef={(element) => { textarea = element; }}
            value={draft()}
            rows={2}
            resize="none"
            placeholder={reviewingPlan() ? "Describe changes to the plan…" : "Message Hames"}
            aria-label="Message Hames"
            aria-expanded={commandMenuOpen() || actionsOpen()}
            aria-haspopup="listbox"
            onInput={(event) => {
              setDraft(event.currentTarget.value);
              setActionsOpen(false);
              setRequestedCommands(false);
              setDismissedSlashDraft("");
            }}
            onKeyDown={(event) => {
              if (commandMenuOpen()) {
                const options = visibleCommands();
                if (event.key === "ArrowDown" && options.length > 0) {
                  event.preventDefault();
                  setSelectedCommandIndex((index) => (index + 1) % options.length);
                  return;
                }
                if (event.key === "ArrowUp" && options.length > 0) {
                  event.preventDefault();
                  setSelectedCommandIndex((index) => (index - 1 + options.length) % options.length);
                  return;
                }
                if (event.key === "Home" && options.length > 0) {
                  event.preventDefault();
                  setSelectedCommandIndex(0);
                  return;
                }
                if (event.key === "End" && options.length > 0) {
                  event.preventDefault();
                  setSelectedCommandIndex(options.length - 1);
                  return;
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  setRequestedCommands(false);
                  setDismissedSlashDraft(draft());
                  return;
                }
                if (event.key === "Enter" && !event.shiftKey && !event.isComposing && options.length > 0) {
                  event.preventDefault();
                  insertCommand(options[selectedCommandIndex()] ?? options[0]!);
                  return;
                }
              }
              if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
              event.preventDefault();
              void submit();
            }}
          />
          <Show when={composerError()}>
            <div class="composer-feedback" aria-live="polite">
              <Show when={composerError()}>
                <span class="composer-error">{composerError()}</span>
                <Show when={trustRequired()}>
                  <Button
                    variant="quiet"
                    class="composer-trust-action"
                    type="button"
                    loading={trusting()}
                    disabled={trusting() || sending()}
                    onClick={() => void trustAndRetry()}
                  >
                    Trust workspace &amp; retry
                  </Button>
                </Show>
              </Show>
            </div>
          </Show>
          <div class="composer-toolbar">
          <ComposerActionMenu
            open={actionsOpen()}
            disabled={sending() || executingPlan()}
            onUpload={() => {
              setActionsOpen(false);
              attachmentInput.click();
            }}
            onCommands={openCommands}
            onResumePlan={resumablePlan() && !props.activeRunId ? () => { setActionsOpen(false); void approvePlan(); } : undefined}
            resumeDisabled={!!draft().trim() || attachments().length > 0}
          />

            <ComposerSeat
              seat="left"
              session={props.session}
              disabled={sending() || executingPlan()}
              onSessionUpdated={props.onSessionUpdated}
              onError={reportControlError}
              onOpenActions={openActions}
            />
            <div class="composer-toolbar-spacer" />
            <ComposerSeat
              seat="right"
              session={props.session}
              disabled={sending() || executingPlan()}
              onSessionUpdated={props.onSessionUpdated}
              onError={reportControlError}
              onOpenActions={openActions}
            />
            <Button
              variant="bare"
              class={`composer-round ${props.activeRunId ? "stop" : "send"}`}
              type="button"
              aria-label={props.activeRunId ? "Stop" : "Send message"}
              title={props.activeRunId ? "Stop" : "Send message"}
              disabled={props.activeRunId ? cancelling() : (!draft().trim() && attachments().length === 0) || sending() || executingPlan()}
              onClick={() => void (props.activeRunId ? cancel() : submit())}
            >
              <Show when={props.activeRunId} fallback={<Icon name="action.send" size={18} />}>
                <Icon name="action.stop" size={14} />
              </Show>
            </Button>
          </div>
        </div>
      </div>
      <Show when={props.showStats !== false}>
        <ComposerStatsLine
          session={props.session}
          activeRunId={props.activeRunId}
        />
      </Show>
      <Show when={previewing()} keyed>
        {(item) => <Lightbox item={item} onClose={() => setPreviewing(undefined)} />}
      </Show>
    </div>
  );
}
