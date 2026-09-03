import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const bootstrap = {
  protocol_version: 1,
  gateway_protocol_version: 33,
  working_directory: "/work/hames",
  csrf_token: "csrf",
};

const health = {
  status: "ok",
  version: "0.0.0",
  protocol_version: 33,
  database_ready: true,
  provider_profiles: ["codex"],
  default_provider: "codex",
  active_runs: 1,
  active_terminals: 2,
  mcp_servers: 1,
  mcp_ready: 1,
  mcp_degraded: 0,
};

const sessions = [
  {
    id: "session-current",
    created_at: "2026-09-02T18:00:00Z",
    status: "active",
    title: "Build the web foundation",
    working_directory: "/work/hames",
    agent_id: "default",
    provider: "codex",
    model: "gpt-5.6-sol",
    reasoning_effort: "high",
    interaction_mode: "auto",
  },
  {
    id: "session-other",
    created_at: "2026-09-01T18:00:00Z",
    status: "active",
    title: "Another project",
    working_directory: "/work/elsewhere",
    agent_id: "default",
    provider: "codex",
    model: "gpt-5.6-sol",
    reasoning_effort: "high",
    interaction_mode: "auto",
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function successfulFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/_hames/v1/bootstrap") return jsonResponse(bootstrap);
    if (path === "/v1/health") return jsonResponse(health);
    if (path === "/v1/sessions") return jsonResponse(sessions);
    return jsonResponse({ error: { message: "not found" } }, 404);
  });
}

describe("Hames web shell", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/chat");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders live workspace sessions without leaking other projects", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    expect(screen.getByRole("heading", { name: "Conversations" })).toBeInTheDocument();
    expect(await screen.findByText("Build the web foundation")).toBeInTheDocument();
    expect(screen.queryByText("Another project")).not.toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-icon-pack="phosphor"]')).toHaveLength(8);
  });

  it("provides every planned top-level area through handcrafted navigation", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);
    await screen.findByText("Build the web foundation");

    for (const label of [
      "Chat",
      "Runs",
      "Agents",
      "Memory",
      "Skills",
      "Scars",
      "Plugins",
      "Settings",
    ]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole("link", { name: "Runs" }));
    expect(await screen.findByRole("heading", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByText("This surface is intentionally quiet for now.")).toBeInTheDocument();
  });

  it("surfaces a retryable offline state", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("connection refused"))
      .mockImplementation(successfulFetch());
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("connection refused");
    fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
    await waitFor(() => expect(screen.getAllByText("Connected").length).toBeGreaterThan(0));
  });
});
