import { fireEvent, render, screen } from "@solidjs/testing-library";
import { expect, it, vi } from "vitest";
import { DelegationChatLink } from "./DelegationChatLink";
it("opens the worker transcript inside its parent chat instead of navigating", () => {
  const listener = vi.fn(); window.addEventListener("hames:open-worker", listener);
  const { unmount } = render(() => <DelegationChatLink node={{ id: "request", kind: "delegation", runId: "run", agentId: "qwen-builder", parentSessionId: "parent", model: "qwen", effort: "", status: "working" }} />);
  try {
    fireEvent.click(screen.getByRole("button", { name: "View transcript" }));
    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0]![0] as CustomEvent).detail).toEqual({ sessionId: "parent", agentId: "qwen-builder" });
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/separate chat/)).not.toBeInTheDocument();
  } finally { unmount(); window.removeEventListener("hames:open-worker", listener); }
});
