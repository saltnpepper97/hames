import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly listeners = new Map<string, EventListener[]>();
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  close(): void {}
}

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
    status: "open",
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
    status: "open",
    title: "Another project",
    working_directory: "/work/elsewhere",
    agent_id: "default",
    provider: "codex",
    model: "gpt-5.6-sol",
    reasoning_effort: "high",
    interaction_mode: "auto",
  },
  {
    id: "session-closed",
    created_at: "2026-08-31T18:00:00Z",
    status: "closed",
    title: "Closed workspace chat",
    working_directory: "/work/hames",
    agent_id: "default",
    provider: "codex",
    model: "gpt-5.6-sol",
    reasoning_effort: "high",
    interaction_mode: "auto",
  },
];

const createdSession = {
  ...sessions[0],
  id: "session-new",
  created_at: "2026-09-02T19:00:00Z",
  title: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function successfulFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/_hames/v1/bootstrap") return jsonResponse(bootstrap);
    if (path === "/v1/health") return jsonResponse(health);
    if (path === "/v1/sessions?has_messages=true") return jsonResponse(sessions);
    if (path === "/v1/sessions" && init?.method === "POST") {
      return jsonResponse(createdSession, 201);
    }
    if (path === "/v1/sessions/session-current/messages" && init?.method === "POST") {
      return jsonResponse(
        {
          submission_id: "submission-one",
          replayed: false,
          disposition: "started",
          run_id: "run-one",
          queued: null,
        },
        202,
      );
    }
    if (path === "/v1/runs/run-one/cancel" && init?.method === "POST") {
      return jsonResponse({ cancelled: true });
    }
    if (path === "/v1/approvals/approval-one" && init?.method === "POST") {
      return jsonResponse({ status: "approved" });
    }
    if (path === "/v1/questions/question-one" && init?.method === "POST") {
      return jsonResponse({ answer: "Proceed" });
    }
    return jsonResponse({ error: { message: "not found" } }, 404);
  });
}

function durableEvent(
  type: string,
  sequence: number,
  payload: Record<string, unknown>,
  runId: string | null = "run-one",
) {
  return {
    durable: true,
    event: {
      id: `event-${sequence}`,
      sequence,
      session_id: "session-current",
      run_id: runId,
      agent_id: "default",
      type,
      schema_version: 1,
      created_at: "2026-09-02T18:00:00Z",
      causation_id: null,
      correlation_id: null,
      payload,
      blob_hash: null,
      payload_hash: `hash-${sequence}`,
      redaction_state: "clear",
    },
  };
}

describe("Hames web shell", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/chat");
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders real workspace chats in the contextual sidebar", async () => {
    vi.stubGlobal("fetch", successfulFetch());
    render(() => <App />);

    expect(await screen.findByText("Build the web foundation")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Select a chat", level: 1 })).toBeInTheDocument();
    expect(screen.queryByText("Another project")).not.toBeInTheDocument();
    expect(screen.queryByText("Closed workspace chat")).not.toBeInTheDocument();
    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-icon^="nav."]')).toHaveLength(8);
    expect(document.querySelector('[data-icon="nav.scars"]')).toHaveAttribute(
      "data-icon-pack",
      "hames-default",
    );
    expect(document.querySelector('[data-icon="nav.scars"] svg')).toHaveClass(
      "tabler-icon-bandage",
    );
    expect(document.querySelector('[data-icon="nav.plugins"] svg')).toHaveClass(
      "tabler-icon-plug",
    );
    expect(document.querySelector('[data-icon="nav.agents"] .phosphor-icon')).toHaveClass(
      "phosphor-icon",
    );
    expect(document.querySelector('[data-icon="nav.memory"] .phosphor-icon')).toBeInTheDocument();
    expect(document.querySelector('[data-icon="brand.mark"] .phosphor-icon')).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: /Build the web foundation/ }));
    expect(
      await screen.findByRole("heading", { name: "Build the web foundation", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Build the web foundation" }).parentElement)
      .toHaveTextContent("gpt-5.6-sol");
  });

  it("replays live gateway events and submits messages", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0]!;
    expect(source.url).toBe("/v1/events?session_id=session-current");
    source.open();
    source.emit("user.message", durableEvent("user.message", 1, { content: "Hello", purpose: "turn" }));
    source.emit("run.started", durableEvent("run.started", 2, {}));
    source.emit("response.text_delta", {
      durable: false,
      session_id: "session-current",
      run_id: "run-one",
      type: "response.text_delta",
      payload: { text: "Hi there" },
    });

    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("Hi there")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();

    fireEvent.input(screen.getByRole("textbox", { name: "Message Hames" }), {
      target: { value: "Follow up" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));
    expect(await screen.findByText("Message sent")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/messages",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/runs/run-one/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("creates a durable chat and opens its composer", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    await screen.findByText("Build the web foundation");

    fireEvent.click(screen.getByRole("button", { name: "New chat" }));

    expect(await screen.findByRole("heading", { name: "New chat" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/chat/session-new");
    expect(screen.getByRole("textbox", { name: "Message Hames" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ working_directory: "/work/hames" }),
      }),
    );
  });

  it("resolves approval and question events through gateway mutations", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0]!;

    source.emit(
      "approval.requested",
      durableEvent("approval.requested", 1, {
        approval_id: "approval-one",
        tool_call_id: "tool-one",
        name: "shell",
        arguments: { command: "cargo test" },
        request_hash: "a".repeat(64),
        working_directory: "/work/hames",
        reason: "Run the test suite",
        allow_session: true,
      }),
    );
    source.emit(
      "question.requested",
      durableEvent("question.requested", 2, {
        question_id: "question-one",
        tool_call_id: "tool-two",
        question: "Continue with the change?",
        options: [{ label: "Proceed", description: "Continue implementation" }],
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Allow once" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/approvals/approval-one",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /Proceed/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/questions/question-one",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("provides every core plugin surface through the icon rail", async () => {
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
    expect(await screen.findByRole("heading", { name: "Runs", level: 1 })).toBeInTheDocument();
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

  it("distinguishes an expired browser session from an offline gateway", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          { error: { code: "web_session_required", message: "session expired" } },
          401,
        ),
      ),
    );
    render(() => <App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Run hames web to reconnect securely",
    );
    expect(screen.queryByRole("button", { name: "Retry connection" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Reopen Hames Web").length).toBeGreaterThan(0);
  });
});
