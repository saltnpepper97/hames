import { cleanup, render, screen } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { afterEach, expect, it, vi } from "vitest";
import { ChatSessionBar } from "./ChatSessionBar";
import { IconProvider } from "../../shell/icons";
import { hamesIconPack } from "../../plugins/icons/hames";
import type { Session } from "../../api/types";
vi.mock("./AgentPicker", () => ({ AgentPicker: () => <div /> }));
vi.mock("./ChatViewTabs", () => ({ ChatViewTabs: () => <div /> }));
afterEach(cleanup);
it("keeps the latest flow accessible after completion until cleared", () => {
  const [status, setStatus] = createSignal<string>();
  render(() => <IconProvider pack={hamesIconPack}><ChatSessionBar session={{ title: "Main chat" } as Session} view="chat" streamState="live" working={false} flowStatus={status()} onFlowToggle={() => {}} onViewChanged={() => {}} onSessionUpdated={() => {}} /></IconProvider>);
  expect(screen.queryByRole("button", { name: "Flow agents" })).not.toBeInTheDocument();
  for (const state of ["running", "waiting", "paused", "needs_attention"]) {
    setStatus(state);
    const button = screen.getByRole("button", { name: "Flow agents" });
    expect(button.textContent).toBe("");
    expect(button.querySelector('[data-icon="nav.flows"]')).toBeInTheDocument();
  }
  setStatus("completed");
  expect(screen.getByRole("button", { name: "Flow agents" })).toBeInTheDocument();
  setStatus(undefined);
  expect(screen.queryByRole("button", { name: "Flow agents" })).not.toBeInTheDocument();
});
