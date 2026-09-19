import { fireEvent, render, screen } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { beforeEach, expect, it, vi } from "vitest";
import { AgentDefaultModel } from "./AgentDefaultModel";
import type { AgentDetail } from "../api/types";
const api = vi.hoisted(() => ({listProviders: vi.fn(), probeProvider: vi.fn()}));
vi.mock("../api/client", () => api);
beforeEach(() => {
  vi.resetAllMocks();
  api.listProviders.mockResolvedValue([{id: "codex"}]);
  api.probeProvider.mockResolvedValue({reachable: true, models: [
    {id: "luna", reasoning_supported: true, reasoning_efforts: ["medium", "xhigh"]},
  ]});
});

it("lets users choose a model and effort, then clear the default", async () => {
  const [value, setValue] = createSignal<AgentDetail["default_model"]>(null);
  render(() => <AgentDefaultModel value={value()} onChange={setValue} />);
  await screen.findByRole("button", { name: "Refresh" });
  fireEvent.click(screen.getByRole("combobox", { name: "Default model" }));
  fireEvent.click(await screen.findByRole("option", { name: "codex/luna" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Default reasoning effort" }));
  fireEvent.click(screen.getByRole("option", { name: "xhigh" }));
  expect(value()).toEqual({ provider: "codex", model: "luna", reasoning_effort: "xhigh" });
  fireEvent.click(screen.getByRole("combobox", { name: "Default model" }));
  fireEvent.click(screen.getByRole("option", { name: "No default — use chat model" }));
  expect(value()).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Default reasoning effort" })).toBeNull();
});

it("waits for authentication, loads automatically, and refreshes on demand", async () => {
  const [ready, setReady] = createSignal(false);
  render(() => <AgentDefaultModel ready={ready()} value={null} onChange={() => {}} />);
  expect(api.listProviders).not.toHaveBeenCalled();
  setReady(true);
  fireEvent.click(screen.getByRole("combobox", {name: "Default model"}));
  await screen.findByRole("option", {name: "codex/luna"});
  expect(api.probeProvider).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("option", {name: "No default — use chat model"}));
  fireEvent.click(screen.getByRole("button", {name: "Refresh"}));
  await vi.waitFor(() => expect(api.probeProvider).toHaveBeenCalledTimes(2));
});


it("does not offer unsupported reasoning-off for GLM-5.3", async () => {
  api.probeProvider.mockResolvedValue({ reachable: true, models: [
    { id: "glm-5.3", reasoning_supported: true, reasoning_efforts: ["high"] },
  ] });
  render(() => <AgentDefaultModel value={{ provider: "codex", model: "glm-5.3", reasoning_effort: "" }} onChange={() => {}} />);
  await vi.waitFor(() => expect(api.probeProvider).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("combobox", { name: "Default reasoning effort" }));
  await screen.findByRole("option", { name: "high" });
  expect(screen.queryByRole("option", { name: "off" })).not.toBeInTheDocument();
});
