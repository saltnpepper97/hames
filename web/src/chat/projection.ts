import type { HamesEvent, MessageAttachment } from "../api/types";

export type DelegationNode = {
  id: string; kind: "delegation"; runId: string; agentId: string; parentSessionId?: string;
  model: string; effort: string; status: string;
};

export type ConversationNode =
  | DelegationNode
  | {
      id: string;
      kind: "context";
      eventId: string;
      provider: string;
      model: string;
      estimatedTokens: number;
      selectedSources: PromptSource[];
      omittedSources: PromptSource[];
    }
  | {
      id: string;
      kind: "compaction";
      compactionId: string;
      status: "running" | "completed" | "failed" | "cancelled";
      trigger: string;
      summary: string;
      message: string;
      turnsCompacted: number;
      beforeTokens: number;
      afterTokens: number;
      partial: boolean;
    }
  | {
      id: string;
      kind: "maintenance";
      maintenanceId: string;
      category: "wrap-up" | "dream";
      label: string;
      phase: "queued" | "running" | "paused" | "completed" | "failed";
      detail: string;
    }
  | {
      id: string;
      kind: "user" | "assistant" | "reasoning";
      content: string;
      live?: boolean;
      agentId?: string;
      sessionId?: string;
      attachments?: MessageAttachment[];
    }
  | {
      id: string;
      kind: "tool";
      sessionId: string;
      workingDirectory: string;
      name: string;
      status: string;
      arguments?: Record<string, unknown>;
      summary?: string;
      content?: string;
      structuredData?: Record<string, unknown>;
      truncated?: boolean;
      durationSeconds?: number;
    }
  | {
      id: string;
      kind: "notice";
      tone: "neutral" | "danger";
      content: string;
    }
  | {
      id: string;
      kind: "approval";
      approvalId: string;
      requestHash: string;
      name: string;
      reason: string;
      arguments: Record<string, unknown>;
      allowSession: boolean;
      status: string;
    }
  | {
      id: string;
      kind: "question";
      questionId: string;
      question: string;
      answerType: "single_choice" | "multiple_choice" | "text";
      options: { label: string; description: string }[];
      minSelections: number;
      maxSelections: number;
      placeholder: string;
      status: string;
      answer?: string;
    };

export interface LiveOutput {
  runId: string;
  afterSequence?: number;
  reasoning: string;
  text: string;
}

export type SessionTaskStatus = "pending" | "in_progress" | "completed" | "blocked";

export interface SessionTaskItem {
  id: string;
  text: string;
  status: SessionTaskStatus;
  position: number;
}

export interface SessionTaskProjection {
  title: string;
  revision: number;
  items: SessionTaskItem[];
  updatedAt: string;
}

export interface ConversationProjection {
  nodes: ConversationNode[];
  tasks: SessionTaskProjection;
  activeRunId?: string;
  activeWorker?: DelegationNode;
}

export interface PromptSource {
  id: string;
  type: string;
  tokens: number;
  estimatedTokens: number;
  path: string;
  memoryId: string;
  memoryLayer: string;
  skillSlug: string;
  reason: string;
  truncation: string;
}

function text(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

function toolIdentity(event: HamesEvent): string {
  return text(event.payload, "tool_call_id") || event.id;
}

function number(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function promptSources(value: unknown): PromptSource[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
    const source = entry as Record<string, unknown>;
    return [{
      id: text(source, "source_id"),
      type: text(source, "source_type"),
      tokens: number(source, "selected_tokens"),
      estimatedTokens: number(source, "estimated_tokens"),
      path: text(source, "source_path"),
      memoryId: text(source, "memory_id"),
      memoryLayer: text(source, "memory_layer"),
      skillSlug: text(source, "skill_slug"),
      reason: text(source, "reason"),
      truncation: text(source, "truncation"),
    }];
  });
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function canonicalContext(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalContext);
  const object = record(value);
  return object
    ? Object.fromEntries(Object.keys(object).sort().map((key) => [key, canonicalContext(object[key])]))
    : value;
}

function promptContextKey(payload: Record<string, unknown>): string {
  // Request hashes/totals and history grow on every call. Compare the attributed
  // context instead, keeping hashes, selection, truncation, provenance and order.
  const context = { ...payload };
  for (const key of [
    "request_hash", "request_snapshot_blob_hash", "estimated_input_tokens",
    "contributing_event_ids",
  ]) delete context[key];
  const historyIds = new Set<string>();
  for (const key of ["selected_sources", "omitted_sources"]) {
    if (Array.isArray(context[key])) {
      context[key] = context[key].filter((source: unknown) => {
        const type = record(source)?.source_type;
        if (type === "conversation" || type === "reasoning") {
          historyIds.add(String(record(source)?.source_id));
        }
        return type !== "conversation" && type !== "reasoning";
      });
    }
  }
  if (Array.isArray(context.source_order)) {
    context.source_order = context.source_order.filter((id) => !historyIds.has(String(id)));
  }
  // Capture time is audit metadata; rendered environment changes are also hashed
  // in runtime.environment. Keep the remaining facts for older manifests too.
  const environment = record(context.environment);
  if (environment) {
    context.environment = { ...environment };
    delete (context.environment as Record<string, unknown>).observed_at;
    const workspace = record((context.environment as Record<string, unknown>).workspace);
    if (workspace) {
      const next = { ...workspace };
      delete next.dirty;
      delete next.changed_files;
      (context.environment as Record<string, unknown>).workspace = next;
    }
  }
  return JSON.stringify(canonicalContext(context));
}

function messageAttachments(value: unknown): MessageAttachment[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const attachment = record(entry);
    if (!attachment) return [];
    const kind = text(attachment, "kind");
    const digest = text(attachment, "digest");
    const name = text(attachment, "name");
    if (!digest || !name || (kind !== "image" && kind !== "text")) return [];
    return [{
      digest,
      name,
      media_type: text(attachment, "media_type"),
      kind,
      size: number(attachment, "size"),
    }];
  });
}

function taskItem(value: unknown): SessionTaskItem | undefined {
  const item = record(value);
  if (!item) return undefined;
  const id = text(item, "id");
  const taskText = text(item, "text");
  const status = text(item, "status");
  if (!id || !taskText || !["pending", "in_progress", "completed", "blocked"].includes(status)) {
    return undefined;
  }
  return {
    id,
    text: taskText,
    status: status as SessionTaskStatus,
    position: number(item, "position"),
  };
}

function positioned(items: readonly SessionTaskItem[]): SessionTaskItem[] {
  return [...items]
    .sort((left, right) => left.position - right.position)
    .map((item, position) => ({ ...item, position }));
}

type MaintenanceNode = Extract<ConversationNode, { kind: "maintenance" }>;

function wrapUpDetail(
  label: string,
  phase: MaintenanceNode["phase"],
  payload: Record<string, unknown>,
): string {
  if (phase === "failed") {
    const message = text(payload, "error_message");
    return message ? `${label} paused · ${message}` : `${label} paused`;
  }
  if (phase === "queued") {
    return text(payload, "status") === "running"
      ? `${label} waiting for the idle model`
      : `${label} queued`;
  }
  if (phase === "running") {
    if (label === "Memory update") return "Reviewing this turn for durable memory";
    if (label === "Requested memory capture") return "Capturing requested memory in the background";
    if (label === "Skill update") return "Creating or refining a reusable Skill";
    return `${label} in the background`;
  }
  if (phase === "completed") {
    if (label === "Memory update") return "Turn memory reviewed";
    if (label === "Requested memory capture") return "Requested memory captured";
    if (label === "Skill update") return "Skill update complete";
    return `${label} complete`;
  }
  return `${label} paused for foreground work`;
}

function dreamDetail(
  phase: MaintenanceNode["phase"],
  payload: Record<string, unknown>,
): string {
  if (phase === "running") return "Reviewing recent memory, Skills, and scars";
  if (phase === "paused") return "Dream paused for foreground work";
  if (phase === "failed") return text(payload, "message") || "Dream maintenance failed";
  if (phase !== "completed") return "Waiting for idle time";
  const memories = number(payload, "memories_reconciled");
  const skills = number(payload, "skills_reconciled");
  const scars = number(payload, "scars_repaired");
  if (memories === 0 && skills === 0 && scars === 0) {
    return "Recent memory, Skills, and scars are already tidy";
  }
  const parts: string[] = [];
  if (memories > 0) parts.push(`${memories} ${memories === 1 ? "memory" : "memories"}`);
  if (skills > 0) parts.push(`${skills} ${skills === 1 ? "Skill" : "Skills"}`);
  if (scars > 0) parts.push(`${scars} ${scars === 1 ? "scar" : "scars"}`);
  return `Reconciled ${parts.join(" · ")}`;
}

function taskList(
  value: unknown,
  fallback: SessionTaskProjection,
  updatedAt = fallback.updatedAt,
): SessionTaskProjection | undefined {
  const candidate = record(value);
  if (!candidate || !Array.isArray(candidate.items)) return undefined;
  return {
    title: text(candidate, "title") || fallback.title,
    revision: number(candidate, "revision") || fallback.revision,
    items: positioned(candidate.items.flatMap((item) => {
      const parsed = taskItem(item);
      return parsed ? [parsed] : [];
    })),
    updatedAt: text(candidate, "updated_at") || updatedAt,
  };
}

function projectTaskEvent(
  current: SessionTaskProjection,
  event: HamesEvent,
): SessionTaskProjection {
  if (event.type === "tasks.replaced") {
    return taskList(event.payload, current, event.created_at) ?? current;
  }
  if (event.type === "task.added") {
    const item = taskItem(event.payload.task);
    if (!item) return current;
    const items = [...current.items];
    items.splice(Math.min(Math.max(item.position, 0), items.length), 0, item);
    return { ...current, items: positioned(items), updatedAt: event.created_at };
  }
  if (event.type === "task.updated") {
    const taskId = text(event.payload, "task_id");
    const index = current.items.findIndex((item) => item.id === taskId);
    if (index < 0) return current;
    const items = [...current.items];
    const existing = items.splice(index, 1)[0]!;
    const status = text(event.payload, "status");
    const nextStatus = ["pending", "in_progress", "completed", "blocked"].includes(status)
      ? status as SessionTaskStatus
      : existing.status;
    const updated = {
      ...existing,
      text: text(event.payload, "text") || existing.text,
      status: nextStatus,
    };
    if (nextStatus === "in_progress") {
      for (let itemIndex = 0; itemIndex < items.length; itemIndex += 1) {
        const item = items[itemIndex]!;
        if (item.status === "in_progress") items[itemIndex] = { ...item, status: "pending" };
      }
    }
    const requestedPosition = event.payload.position;
    const nextPosition = typeof requestedPosition === "number" && Number.isFinite(requestedPosition)
      ? Math.min(Math.max(requestedPosition, 0), items.length)
      : Math.min(index, items.length);
    items.splice(nextPosition, 0, updated);
    return { ...current, items: positioned(items), updatedAt: event.created_at };
  }
  if (event.type === "task.removed") {
    const taskId = text(event.payload, "task_id");
    return {
      ...current,
      items: positioned(current.items.filter((item) => item.id !== taskId)),
      updatedAt: event.created_at,
    };
  }
  return current;
}

export function projectConversation(
  events: readonly HamesEvent[],
  workingDirectory = "",
): ConversationProjection {
  const nodes: ConversationNode[] = [];
  const delegations = new Map<string, DelegationNode>();
  const tools = new Map<string, Extract<ConversationNode, { kind: "tool" }>>();
  const approvals = new Map<string, Extract<ConversationNode, { kind: "approval" }>>();
  const questions = new Map<string, Extract<ConversationNode, { kind: "question" }>>();
  const compactions = new Map<string, Extract<ConversationNode, { kind: "compaction" }>>();
  const maintenance = new Map<string, MaintenanceNode>();
  const activeRuns = new Set<string>();
  const promptContexts = new Map<string, string>();
  let tasks: SessionTaskProjection = { title: "Tasks", revision: 0, items: [], updatedAt: "" };

  for (const event of events) {
    if (event.type === "run.started" && event.run_id) activeRuns.add(event.run_id);
    if (
      ["run.completed", "run.failed", "run.cancelled"].includes(event.type) &&
      event.run_id
    ) {
      activeRuns.delete(event.run_id);
      for (const node of delegations.values()) {
        if (node.runId === event.run_id && node.status === "working") {
          node.status = event.type === "run.cancelled" ? "cancelled" : "interrupted";
        }
      }
    }

    if (event.type === "delegation.requested") {
      const node: DelegationNode = {
        id: event.id, kind: "delegation", runId: event.run_id || "", parentSessionId: event.session_id,
        agentId: text(event.payload, "target_agent_id"),
        model: [text(event.payload, "provider"), text(event.payload, "model")].filter(Boolean).join("/"),
        effort: text(event.payload, "reasoning_effort"), status: "working",
      };
      delegations.set(event.id, node);
      nodes.push(node);
      continue;
    }
    if (["delegation.completed", "delegation.failed", "delegation.stopping"].includes(event.type)) {
      const node = delegations.get(event.causation_id || "");
      if (node) node.status = text(event.payload, "status") || (event.type === "delegation.completed" ? "completed" : "failed");
      continue;
    }

    if (event.type.startsWith("context.compaction.")) {
      const compactionId = text(event.payload, "compaction_id") || event.run_id || event.id;
      let compaction = compactions.get(compactionId);
      if (!compaction) {
        compaction = {
          id: `compaction-${compactionId}`,
          kind: "compaction",
          compactionId,
          status: "running",
          trigger: text(event.payload, "trigger"),
          summary: "",
          message: "",
          turnsCompacted: 0,
          beforeTokens: 0,
          afterTokens: 0,
          partial: false,
        };
        compactions.set(compactionId, compaction);
        nodes.push(compaction);
      }
      if (event.type === "context.compaction.started") {
        compaction.status = "running";
        activeRuns.add(compactionId);
      } else {
        activeRuns.delete(compactionId);
        compaction.status = event.type.split(".").at(-1) as "completed" | "failed" | "cancelled";
      }
      compaction.trigger = text(event.payload, "trigger") || compaction.trigger;
      compaction.summary = text(event.payload, "summary") || compaction.summary;
      compaction.message = text(event.payload, "message") || compaction.message;
      compaction.turnsCompacted = number(event.payload, "turns_compacted") || compaction.turnsCompacted;
      compaction.beforeTokens = number(event.payload, "before_tokens") || compaction.beforeTokens;
      compaction.afterTokens = number(event.payload, "after_tokens") || compaction.afterTokens;
      compaction.partial = event.payload.partial === true;
      continue;
    }

    if (event.type === "model.response.started" && event.correlation_id) {
      const job = maintenance.get(event.correlation_id);
      if (job?.category === "wrap-up") {
        job.phase = "running";
        job.detail = wrapUpDetail(job.label, "running", event.payload);
        continue;
      }
    }

    if (event.type.startsWith("memory.job.") || event.type.startsWith("skill.job.")) {
      const maintenanceId = text(event.payload, "job_id") || event.correlation_id || event.id;
      const label = event.type.startsWith("skill.job.")
        ? "Skill update"
        : text(event.payload, "kind") === "explicit_capture"
          ? "Requested memory capture"
          : "Memory update";
      const suffix = event.type.split(".").at(-1);
      const phase: MaintenanceNode["phase"] = suffix === "completed"
        ? "completed"
        : suffix === "paused"
          ? "paused"
          : suffix === "failed"
            ? "failed"
            : "queued";
      let job = maintenance.get(maintenanceId);
      if (!job) {
        job = {
          id: `maintenance-${maintenanceId}`,
          kind: "maintenance",
          maintenanceId,
          category: "wrap-up",
          label,
          phase,
          detail: wrapUpDetail(label, phase, event.payload),
        };
        maintenance.set(maintenanceId, job);
        nodes.push(job);
      } else {
        job.label = label;
        job.phase = phase;
        job.detail = wrapUpDetail(label, phase, event.payload);
      }
      continue;
    }

    if (event.type.startsWith("dream.")) {
      const maintenanceId = text(event.payload, "dream_id") || event.correlation_id || event.id;
      const suffix = event.type.split(".").at(-1);
      const phase: MaintenanceNode["phase"] = suffix === "started"
        ? "running"
        : suffix === "completed"
          ? "completed"
          : suffix === "paused"
            ? "paused"
            : "failed";
      let dream = maintenance.get(maintenanceId);
      if (!dream) {
        dream = {
          id: `maintenance-${maintenanceId}`,
          kind: "maintenance",
          maintenanceId,
          category: "dream",
          label: "Idle maintenance",
          phase,
          detail: dreamDetail(phase, event.payload),
        };
        maintenance.set(maintenanceId, dream);
        nodes.push(dream);
      } else {
        dream.phase = phase;
        dream.detail = dreamDetail(phase, event.payload);
      }
      continue;
    }

    if (["tasks.replaced", "task.added", "task.updated", "task.removed"].includes(event.type)) {
      tasks = projectTaskEvent(tasks, event);
      continue;
    }

    if (event.type === "user.message") {
      if (text(event.payload, "purpose") !== "plan_execution") {
        nodes.push({
          id: event.id,
          kind: "user",
          content: text(event.payload, "content"),
          sessionId: event.session_id,
          attachments: messageAttachments(event.payload.attachments),
        });
      }
      continue;
    }
    if (event.type === "context.compiled") {
      const scope = JSON.stringify([event.session_id, event.agent_id, event.payload.agent_id]);
      const key = promptContextKey(event.payload);
      if (promptContexts.get(scope) === key) continue;
      promptContexts.set(scope, key);
      nodes.push({
        id: event.id,
        kind: "context",
        eventId: event.id,
        provider: text(event.payload, "provider"),
        model: text(event.payload, "model"),
        estimatedTokens: number(event.payload, "estimated_input_tokens"),
        selectedSources: promptSources(event.payload.selected_sources),
        omittedSources: promptSources(event.payload.omitted_sources),
      });
      continue;
    }
    if (event.type === "assistant.reasoning") {
      const content = text(event.payload, "content");
      if (content) {
        nodes.push({
          id: event.id,
          kind: "reasoning",
          content,
          agentId: event.agent_id ?? undefined,
        });
      }
      continue;
    }
    if (event.type === "assistant.message") {
      const content = text(event.payload, "content");
      if (content) {
        nodes.push({
          id: event.id,
          kind: "assistant",
          content,
          agentId: event.agent_id ?? undefined,
        });
      }
      continue;
    }
    if (["model.tool_call", "tool.requested", "tool.started"].includes(event.type)) {
      const id = toolIdentity(event);
      let tool = tools.get(id);
      if (!tool) {
        tool = {
          id: `tool-${id}`,
          kind: "tool",
          sessionId: event.session_id,
          workingDirectory,
          name: text(event.payload, "name") || "Tool",
          status: text(event.payload, "status") || event.type.split(".")[1] || "pending",
        };
        tools.set(id, tool);
        nodes.push(tool);
      }
      tool.name = text(event.payload, "name") || tool.name;
      tool.status = text(event.payload, "status") || event.type.split(".")[1] || tool.status;
      const argumentsValue = event.payload.arguments;
      if (argumentsValue && typeof argumentsValue === "object" && !Array.isArray(argumentsValue)) {
        tool.arguments = argumentsValue as Record<string, unknown>;
      }
      continue;
    }
    if (["tool.completed", "tool.failed", "tool.rejected"].includes(event.type)) {
      const id = toolIdentity(event);
      let tool = tools.get(id);
      if (!tool) {
        tool = {
          id: `tool-${id}`,
          kind: "tool",
          sessionId: event.session_id,
          workingDirectory,
          name: text(event.payload, "name") || "Tool",
          status: event.type.split(".")[1] || "completed",
        };
        tools.set(id, tool);
        nodes.push(tool);
      }
      tool.status = text(event.payload, "status") || event.type.split(".")[1] || tool.status;
      tool.summary = text(event.payload, "summary");
      tool.content = text(event.payload, "content");
      const structuredData = event.payload.structured_data;
      if (structuredData && typeof structuredData === "object" && !Array.isArray(structuredData)) {
        tool.structuredData = structuredData as Record<string, unknown>;
        tasks = taskList(tool.structuredData.task_list, tasks, event.created_at) ?? tasks;
      }
      tool.truncated = event.payload.truncated === true;
      tool.durationSeconds = number(event.payload, "duration_seconds") || undefined;
      continue;
    }
    if (event.type === "approval.requested") {
      const approvalId = text(event.payload, "approval_id");
      const argumentsValue = event.payload.arguments;
      const approval: Extract<ConversationNode, { kind: "approval" }> = {
        id: event.id,
        kind: "approval",
        approvalId,
        requestHash: text(event.payload, "request_hash"),
        name: text(event.payload, "name") || "Tool",
        reason: text(event.payload, "reason"),
        arguments:
          argumentsValue && typeof argumentsValue === "object" && !Array.isArray(argumentsValue)
            ? (argumentsValue as Record<string, unknown>)
            : {},
        allowSession: event.payload.allow_session === true,
        status: "pending",
      };
      approvals.set(approvalId, approval);
      nodes.push(approval);
      continue;
    }
    if (event.type === "approval.resolved") {
      const approval = approvals.get(text(event.payload, "approval_id"));
      if (approval) approval.status = text(event.payload, "decision") || "resolved";
      continue;
    }
    if (event.type === "question.requested") {
      const questionId = text(event.payload, "question_id");
      const rawOptions = Array.isArray(event.payload.options) ? event.payload.options : [];
      const options = rawOptions.flatMap((option) => {
        if (typeof option === "string") return [{ label: option, description: "" }];
        if (!option || typeof option !== "object" || Array.isArray(option)) return [];
        const value = option as Record<string, unknown>;
        return [{
          label: typeof value.label === "string" ? value.label : "",
          description: typeof value.description === "string" ? value.description : "",
        }];
      }).filter((option) => option.label);
      const requestedType = text(event.payload, "answer_type");
      const answerType = requestedType === "multiple_choice" || requestedType === "text"
        ? requestedType
        : options.length > 0 ? "single_choice" : "text";
      const requestedMinimum = number(event.payload, "min_selections");
      const requestedMaximum = number(event.payload, "max_selections");
      const question: Extract<ConversationNode, { kind: "question" }> = {
        id: event.id,
        kind: "question",
        questionId,
        question: text(event.payload, "question"),
        answerType,
        options,
        minSelections: requestedMinimum || 1,
        maxSelections: requestedMaximum || Math.max(1, options.length),
        placeholder: text(event.payload, "placeholder"),
        status: "pending",
      };
      questions.set(questionId, question);
      nodes.push(question);
      continue;
    }
    if (event.type === "question.answered") {
      const question = questions.get(text(event.payload, "question_id"));
      if (question) {
        question.status = "answered";
        question.answer = text(event.payload, "answer");
      }
      continue;
    }
    if (event.type === "run.failed" || event.type === "runtime.error") {
      const content = text(event.payload, "message");
      if (content) {
        nodes.push({ id: event.id, kind: "notice", tone: "danger", content });
      }
      continue;
    }
    if (event.type === "run.cancelled") {
      nodes.push({ id: event.id, kind: "notice", tone: "neutral", content: "Run cancelled." });
    }
  }

  return { nodes, tasks, activeRunId: [...activeRuns].at(-1),
    activeWorker: [...delegations.values()].reverse().find(node => ["working", "stopping"].includes(node.status)),
  };
}

export function withLiveOutput(
  projection: ConversationProjection,
  live?: LiveOutput,
  agentId?: string,
): ConversationProjection {
  if (!live?.reasoning && !live?.text) return projection;

  const nodes = [...projection.nodes];
  if (live.reasoning) {
    nodes.push({
      id: `live-reasoning-${live.runId}`,
      kind: "reasoning",
      content: live.reasoning,
      live: true,
      agentId,
    });
  }
  if (live.text) {
    nodes.push({
      id: `live-assistant-${live.runId}`,
      kind: "assistant",
      content: live.text,
      live: true,
      agentId,
    });
  }

  return { ...projection, nodes };
}
