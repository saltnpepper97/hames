import { fireEvent, render, screen } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { describe, expect, it } from "vitest";
import { hamesIconPack } from "../../plugins/icons/hames";
import { IconProvider } from "../../shell/icons";
import type { SessionTaskProjection } from "../projection";
import { TaskPanel, createTaskCardState } from "./TaskPanel";

const tasks: SessionTaskProjection = {
  title: "Transcript polish",
  revision: 3,
  updatedAt: "2026-09-04T12:00:00Z",
  items: [
    { id: "one", text: "Build task strip", status: "completed", position: 0 },
    { id: "two", text: "Run web tests", status: "in_progress", position: 1 },
    { id: "three", text: "Inspect mobile layout", status: "pending", position: 2 },
    { id: "four", text: "Resolve visual blocker", status: "blocked", position: 3 },
  ],
};

function DrawerHarness(props: { tasks: SessionTaskProjection }) {
  const state = createTaskCardState(() => "one");
  return <IconProvider pack={hamesIconPack}>
    <TaskPanel tasks={props.tasks} open={state.open()} onToggle={state.toggle} />
  </IconProvider>;
}
function renderPanel(value = tasks) {
  return render(() => <DrawerHarness tasks={value} />);
}

describe("task drawer", () => {
  it("opens with progress and the checklist, then can collapse and reopen", () => {
    renderPanel();

    const panel = screen.getByRole("region", { name: "Transcript polish" });
    expect(panel).toHaveTextContent("1/4 completed");
    fireEvent.click(screen.getByRole("button", { expanded: true }));
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(panel.querySelectorAll("li")).toHaveLength(4);
    expect(panel).not.toHaveAttribute("hidden");
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("img", { name: "Completed" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "In progress" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Pending" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Blocked" })).toBeInTheDocument();
  });

  it("opens on task creation/start, respects collapse and retains completed tasks", () => {
    const [value, setValue] = createSignal({ ...tasks, items: [] as typeof tasks.items });
    render(() => <DrawerHarness tasks={value()} />);
    expect(screen.queryByRole("button", { expanded: false })).not.toBeInTheDocument();
    setValue(tasks);
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { expanded: true }));
    setValue({ ...tasks, revision: 4 });
    expect(screen.getByRole("button", { expanded: false })).toBeInTheDocument();
    setValue({ ...tasks, items: tasks.items.map(task => ({ ...task, status: "completed" })) });
    expect(screen.getByRole("button", { expanded: false })).toBeInTheDocument();
    setValue({ ...tasks, items: tasks.items.map(task => ({ ...task, status: "in_progress" })) });
    expect(screen.getByRole("button", { expanded: false })).toBeInTheDocument();
  });

  it("stays out of the composer when the session has no tasks", () => {
    const { container } = renderPanel({ ...tasks, items: [] });
    expect(container.querySelector(".task-panel")).toBeNull();
  });
});
