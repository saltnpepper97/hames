import { fireEvent, render } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationViewport } from "./ConversationViewport";
import { createSignal } from "solid-js";
import type { ConversationNode } from "../projection";

vi.mock("../../agents/AgentDirectory", () => ({ useAgentDirectory: () => ({ ensureLoaded: async () => {} }) }));
vi.mock("../../shell/pluginContext", () => ({ useWebPlugins: () => ({ conversationNodes: new Map() }) }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("follows settling message heights, respects scrolling into history, and resumes at the bottom", async () => {
  let resized!: () => void;
  let now = 1000;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const [nodes, setNodes] = createSignal<ConversationNode[]>([]);
  const disconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { resized = callback; }
    observe() {}
    disconnect = disconnect;
  });
  const { container, unmount } = render(() => <ConversationViewport nodes={nodes()} streamState="live" agentId="default" />);
  const scroll = container.querySelector<HTMLElement>(".transcript-scroll")!;
  let height = 1000;
  let top = 0;
  Object.defineProperties(scroll, {
    scrollHeight: { get: () => height }, clientHeight: { get: () => 400 },
    scrollTop: { get: () => top, set: (value) => { top = Math.min(value, height - 400); } },
  });
  await Promise.resolve();
  expect(top).toBe(600);
  height = 1400;
  resized();
  expect(top).toBe(1000);
  fireEvent.scroll(scroll);
  // Scrolling down against the bottom emits wheel but no scroll event.
  fireEvent.wheel(scroll, { deltaY: 100 });
  height = 1600;
  resized();
  expect(top).toBe(1200);
  // A user scroll can arrive in the same frame as a bottom-follow write. It
  // must win instead of being discarded by the layout observer.
  scroll.scrollTop = 600;
  fireEvent.scroll(scroll);
  height = 1800;
  resized();
  expect(top).toBe(600);
  scroll.scrollTop = 1400;
  fireEvent.scroll(scroll);
  height = 2000;
  resized();
  expect(top).toBe(1600);
  now += 800;
  resized();
  expect(top).toBe(1600);
  setNodes([{ id: "new", kind: "assistant", content: "New content", live: true }]);
  await Promise.resolve();
  expect(top).toBe(1600);
  fireEvent.wheel(scroll, { deltaY: -10 });
  scroll.scrollTop = 1590;
  fireEvent.scroll(scroll);
  height = 2200;
  setNodes([{ id: "new", kind: "assistant", content: "More content", live: true }]);
  await Promise.resolve();
  resized();
  expect(top).toBe(1590);
  now += 10_000;
  setNodes([{ id: "new", kind: "assistant", content: "Still streaming", live: true }]);
  await Promise.resolve();
  resized();
  expect(top).toBe(1590);
  // A callback already queued by the old chat must not scroll after teardown.
  setNodes([{ id: "new", kind: "assistant", content: "Queued before leaving", live: true }]);
  unmount();
  await Promise.resolve();
  height = 2400;
  resized();
  expect(top).toBe(1590);
  expect(disconnect).toHaveBeenCalled();
});

it("does not run pending auto-follow after leaving the chat", async () => {
  const [nodes, setNodes] = createSignal<ConversationNode[]>([]);
  const { container, unmount } = render(() => <ConversationViewport nodes={nodes()} streamState="live" agentId="default" />);
  const scroll = container.querySelector<HTMLElement>(".transcript-scroll")!;
  let height = 1000;
  let top = 0;
  Object.defineProperties(scroll, {
    scrollHeight: { get: () => height }, clientHeight: { get: () => 400 },
    scrollTop: { get: () => top, set: value => { top = Math.min(value, height - 400); } },
  });
  await Promise.resolve();
  expect(top).toBe(600);
  height = 2000;
  setNodes([{ id: "last", kind: "assistant", content: "Late content", live: true }]);
  unmount();
  await Promise.resolve();
  expect(top).toBe(600);
});

it("restores each chat's reading position through replay and keeps new output from moving it", async () => {
  sessionStorage.setItem("hames.transcript-position:reading", JSON.stringify({ top: 900, follow: false }));
  const [nodes, setNodes] = createSignal<ConversationNode[]>([]);
  let resized!: () => void;
  vi.stubGlobal("ResizeObserver", class { constructor(cb: () => void) { resized = cb; } observe() {} disconnect() {} });
  const { container, unmount } = render(() => <ConversationViewport sessionId="reading" nodes={nodes()} streamState="live" agentId="default" />);
  const scroll = container.querySelector<HTMLElement>(".transcript-scroll")!;
  let height = 400;
  let top = 0;
  Object.defineProperties(scroll, {
    scrollHeight: { get: () => height }, clientHeight: { get: () => 400 },
    scrollTop: { get: () => top, set: value => { top = Math.max(0, Math.min(value, height - 400)); } },
  });
  await Promise.resolve();
  expect(top).toBe(0);
  height = 2000;
  setNodes([{ id: "message", kind: "assistant", content: "History replay", live: false }]);
  await Promise.resolve();
  expect(top).toBe(900);
  height = 3000;
  resized();
  expect(top).toBe(900);
  fireEvent.wheel(scroll, { deltaY: -100 });
  scroll.scrollTop = 800;
  fireEvent.scroll(scroll);
  unmount();
  expect(JSON.parse(sessionStorage.getItem("hames.transcript-position:reading")!)).toEqual({ top: 800, follow: false });
  sessionStorage.removeItem("hames.transcript-position:reading");
});
