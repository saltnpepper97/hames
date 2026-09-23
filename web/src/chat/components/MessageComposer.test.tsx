import { createSignal } from "solid-js";
import { fireEvent, render, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import type { HamesEvent, Session } from "../../api/types";
import { MessageComposer } from "./MessageComposer";

vi.mock("@solidjs/router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../../api/client", () => ({ cancelRun: vi.fn(async () => ({ cancelled: true })) }));
vi.mock("../../shell/workspace", () => ({ useWorkspace: () => ({}) }));
vi.mock("../../shell/pluginContext", () => ({
  useWebPlugins: () => ({ composerControls: new Map(), composerActions: [] }),
}));
vi.mock("../../shell/icons", () => ({ Icon: () => <span /> }));
vi.mock("./MessageQueue", () => ({ MessageQueue: () => null }));

function event(id: string, type: string, payload: Record<string, unknown>): HamesEvent {
  return {
    id, type, payload, sequence: id === "message" ? 1 : 2,
    session_id: "session-one", run_id: type === "user.message" ? null : "run-one",
    agent_id: "default", schema_version: 1, created_at: "2026-09-22T00:00:00Z",
    causation_id: null, correlation_id: null, blob_hash: null,
    payload_hash: id, redaction_state: "clear",
  };
}

afterEach(() => localStorage.removeItem("hames.composer-draft:session-one"));

it("returns a pre-response cancellation to the editable composer", async () => {
  const message = event("message", "user.message", { content: "Try this again" });
  const [events, setEvents] = createSignal<HamesEvent[]>([message]);
  const session = { id: "session-one", interaction_mode: "auto" } as Session;
  const { getByRole } = render(() => <MessageComposer
    session={session} events={events()} activeRunId="run-one" showStats={false}
    onSessionChanged={() => {}} onSessionUpdated={() => {}} onSessionOpened={() => {}}
  />);
  fireEvent.click(getByRole("button", { name: "Stop" }));
  setEvents([message, event("cancelled", "run.cancelled", {
    reason: "stopped", retracted_message_id: message.id,
  })]);
  await waitFor(() => expect((getByRole("textbox", { name: "Message Hames" }) as HTMLTextAreaElement).value)
    .toBe("Try this again"));
});
