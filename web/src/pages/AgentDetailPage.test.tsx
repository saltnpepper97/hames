import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentDirectoryProvider } from "../agents/AgentDirectory";
import { AgentDetailPage } from "./AgentDetailPage";

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AgentDetailPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("does not reset the editor when polling returns the same workspace", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/v1/providers") return response([]);
      if (path === "/v1/agents/default") {
        return response({
          id: "default",
          name: "Hames",
          authority: "standard",
          path: "/agents/default/AGENT.md",
          content_hash: "hash",
          avatar: null,
          source: "# Hames",
          instructions: "# Durable preview",
          tools_allow: [],
          tools_deny: [],
          skills_allow: [],
          skills_deny: [],
          skills_pin: [],
          delegation_allowed: false,
          delegation_targets: [],
          deprecated_fields: [],
        });
      }
      if (path === "/v1/agents/default/capabilities?working_directory=%2Fwork%2Fhames") {
        return response({ tools: [], skills: [] });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const [workspace, setWorkspace] = createSignal({ directory: "/work/hames" });
    render(() => (
      <AgentDirectoryProvider>
        <AgentDetailPage agentId="default" workingDirectory={workspace().directory} />
      </AgentDirectoryProvider>
    ));

    await screen.findByRole("textbox", { name: "Instructions" });
    fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
    expect(screen.getByRole("heading", { name: "Durable preview" })).toBeInTheDocument();

    setWorkspace({ directory: "/work/hames" });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true");
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
