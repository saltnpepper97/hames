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
  gateway_protocol_version: 34,
  working_directory: "/work/hames",
  csrf_token: "csrf",
};

const health = {
  status: "ok",
  version: "0.0.0",
  protocol_version: 34,
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

const agents = [
  {
    id: "default",
    name: "Hames",
    authority: "standard",
    path: "/home/.hames/agents/default/AGENT.md",
    content_hash: "agent-hash-one",
    avatar: null,
  },
  {
    id: "reviewer",
    name: "Reviewer",
    authority: "read_only",
    path: "/home/.hames/agents/reviewer/AGENT.md",
    content_hash: "agent-hash-two",
    avatar: { shape: "arch", eyes: "visor", color: "#0d9488" },
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function successfulFetch() {
  let currentSession = { ...sessions[0] };
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/_hames/v1/bootstrap") return jsonResponse(bootstrap);
    if (path === "/v1/health") return jsonResponse(health);
    if (path === "/v1/sessions?has_messages=true") {
      return jsonResponse([currentSession, ...sessions.slice(1)]);
    }
    if (path === "/v1/agents" && !init?.method) return jsonResponse(agents);
    if (path === "/v1/agents/default" && init?.method === "PATCH") {
      const avatar = JSON.parse(String(init.body)).avatar;
      return jsonResponse({
        ...agents[0],
        avatar,
        source: "---\nid: default\n---\nDefault agent.",
        instructions: "Default agent.",
        tools_allow: [], tools_deny: [], skills_allow: [], skills_deny: [], skills_pin: [],
        delegation_allowed: false, delegation_targets: [], deprecated_fields: [],
      });
    }
    if (path === "/v1/providers") {
      return jsonResponse([
        {
          id: "codex",
          adapter: "codex",
          endpoint: "local",
          configured_model: "gpt-5.6-sol",
          default_reasoning_effort: "high",
          supported_reasoning_efforts: ["low", "medium", "high", "xhigh"],
        },
        {
          id: "ollama",
          adapter: "ollama",
          endpoint: "http://127.0.0.1:11434",
          configured_model: "qwen3:8b",
          default_reasoning_effort: "medium",
          supported_reasoning_efforts: ["low", "medium", "high"],
        },
      ]);
    }
    if (path === "/v1/providers/codex/probe" && init?.method === "POST") {
      return jsonResponse({
        id: "codex",
        adapter: "codex",
        reachable: true,
        models: [{
          id: "gpt-5.6-sol",
          status: "available",
          context_length: 272000,
          parameter_size: null,
          quantization: null,
          reasoning_supported: true,
          reasoning_efforts: ["low", "medium", "high", "xhigh"],
        }],
        error: null,
      });
    }
    if (path === "/v1/providers/ollama/probe" && init?.method === "POST") {
      return jsonResponse({
        id: "ollama",
        adapter: "ollama",
        reachable: true,
        models: [{
          id: "qwen3:8b",
          status: "available",
          context_length: 32768,
          parameter_size: "8B",
          quantization: "Q4_K_M",
          reasoning_supported: true,
          reasoning_efforts: ["low", "medium", "high"],
        }],
        error: null,
      });
    }
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
    if (path === "/v1/sessions/session-current/mode" && init?.method === "PUT") {
      currentSession = { ...currentSession, interaction_mode: "plan" };
      return jsonResponse(currentSession);
    }
    if (path === "/v1/sessions/session-current" && init?.method === "PATCH") {
      const selection = JSON.parse(String(init.body)) as {
        provider: string;
        model: string;
        reasoning_effort: string;
      };
      currentSession = { ...currentSession, ...selection };
      return jsonResponse(currentSession);
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
    expect(document.querySelectorAll('[data-icon^="nav."]')).toHaveLength(7);
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
    fireEvent.click(screen.getByRole("button", { name: "Queue message" }));
    expect(await screen.findByText("Message sent")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/session-current/messages",
      expect.objectContaining({ method: "POST" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) => String(input) === "/v1/sessions?has_messages=true"),
      ).toHaveLength(2),
    );
    expect(MockEventSource.instances).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/runs/run-one/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("updates mode and thinking through plugin-contributed composer controls", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    expect(document.querySelector('[data-icon="mode.auto"] svg')).toHaveClass(
      "tabler-icon-sparkles",
    );
    fireEvent.click(screen.getByRole("button", { name: "Interaction mode" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Plan" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/sessions/session-current/mode",
        expect.objectContaining({ method: "PUT", body: JSON.stringify({ mode: "plan" }) }),
      ),
    );

    await waitFor(() =>
      expect(document.querySelector('[data-icon="mode.plan"] svg')).toHaveClass(
        "tabler-icon-list-check",
      ),
    );

    const selectionTrigger = screen.getByRole("button", { name: /Model and thinking/ });
    fireEvent.click(selectionTrigger);
    fireEvent.click(screen.getByRole("menuitem", { name: /Thinking/ }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: "Medium" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/sessions/session-current",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            provider: "codex",
            model: "gpt-5.6-sol",
            reasoning_effort: "medium",
          }),
        }),
      ),
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Interaction mode" })).toHaveTextContent("Plan");
      expect(screen.getByRole("button", { name: /Model and thinking/ })).toHaveTextContent("Medium");
    });
    expect(screen.queryByRole("button", { name: "Thinking level" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add attachment" })).toBeDisabled();
  });

  it("selects provider, model, and explicit thinking in one model picker flow", async () => {
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);
    fireEvent.click(await screen.findByRole("link", { name: /Build the web foundation/ }));
    await screen.findByRole("heading", { name: "Build the web foundation", level: 1 });

    const modelTrigger = screen.getByRole("button", { name: /Model and thinking/ });
    expect(modelTrigger).toHaveTextContent("gpt-5.6-solHigh");
    fireEvent.click(modelTrigger);
    fireEvent.click(screen.getByRole("menuitem", { name: /Model/ }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: /qwen3:8b/ }));

    expect(screen.getByText("Choose thinking to finish")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH"),
    ).toHaveLength(0);
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Low" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/sessions/session-current",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            provider: "ollama",
            model: "qwen3:8b",
            reasoning_effort: "low",
          }),
        }),
      ),
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Model and thinking/ })).toHaveTextContent(
        "qwen3:8bLow",
      );
    });
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

    for (const label of ["Chat", "Agents", "Memory", "Skills", "Scars", "Plugins", "Settings"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("link", { name: "Runs" })).not.toBeInTheDocument();
  });

  it("lists real agents and persists avatar customization", async () => {
    window.history.replaceState({}, "", "/agents");
    const fetchMock = successfulFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(() => <App />);

    expect(await screen.findByRole("heading", { name: "Hames", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Reviewer", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Read only")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Customize Hames avatar" }));
    expect(screen.getByRole("dialog", { name: "Customize Hames" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Hex/ }));
    fireEvent.click(screen.getByRole("button", { name: /Happy/ }));
    fireEvent.click(screen.getByRole("button", { name: "Use #db2777" }));
    fireEvent.click(screen.getByRole("button", { name: "Save avatar" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents/default",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ avatar: { shape: "hex", eyes: "happy", color: "#db2777" } }),
      }),
    ));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
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
