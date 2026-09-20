import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import type { Accessor } from "solid-js";
import type { SessionTaskItem, SessionTaskProjection } from "../projection";
import { Spinner } from "../../components/Spinner";
import { Icon } from "../../shell/icons";

function taskStatusLabel(status: SessionTaskItem["status"]): string {
  if (status === "in_progress") return "In progress";
  return status.replace(/^./, (letter) => letter.toUpperCase());
}

function TaskStatusGlyph(props: { status: SessionTaskItem["status"] }) {
  return (
    <span
      class="task-panel-glyph"
      data-status={props.status}
      role="img"
      aria-label={taskStatusLabel(props.status)}
    >
      <Show when={props.status === "in_progress"}>
        <Spinner size={14} />
      </Show>
      <Show when={props.status === "completed"}>
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6.5" />
          <path d="m4.9 8.1 2 2 4.3-4.5" />
        </svg>
      </Show>
      <Show when={props.status === "blocked"}>
        <span aria-hidden="true">!</span>
      </Show>
    </span>
  );
}

export function createTaskCardState(sessionId: Accessor<string>) {
  const currentSession = createMemo(sessionId);
  const storageKey = () => `hames.tasks-expanded:${currentSession()}`;
  const read = () => {
    try { return localStorage.getItem(storageKey()) !== "false"; }
    catch { return true; }
  };
  const [open, setOpen] = createSignal(read());
  createEffect(() => { currentSession(); setOpen(read()); });
  const toggle = () => {
    const next = !open();
    setOpen(next);
    try { localStorage.setItem(storageKey(), String(next)); } catch { /* Optional UI state. */ }
  };
  return { open, toggle };
}

export function TaskPanel(props: {
  tasks: SessionTaskProjection;
  open: boolean;
  onToggle: () => void;
  hidden?: boolean;
}) {
  const completed = createMemo(() => props.tasks.items.filter(task => task.status === "completed").length);
  return (
    <Show when={props.tasks.items.length > 0}>
      <section class="task-panel" hidden={props.hidden} aria-label={props.tasks.title || "Tasks"}>
        <button type="button" class="task-panel-header" aria-expanded={props.open} aria-controls="chat-task-list" onClick={props.onToggle}>
          <Icon name="conversation.tasks" size={15} class="task-panel-icon" />
          <span class="task-panel-title">{props.tasks.title || "Tasks"}</span>
          <span class="task-panel-progress" aria-live="polite">{completed()}/{props.tasks.items.length} completed</span>
          <Icon name="action.expand" size={13} class="task-panel-chevron" />
        </button>
        <ul id="chat-task-list" class="task-panel-list" hidden={!props.open}>
          <For each={props.tasks.items}>{task => (
            <li data-status={task.status}>
              <TaskStatusGlyph status={task.status} />
              <span class="task-panel-item-copy" title={task.text}>{task.text}</span>
            </li>
          )}</For>
        </ul>
      </section>
    </Show>
  );
}
