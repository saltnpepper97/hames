import { createRoot, createSignal } from "solid-js";
import { afterEach, expect, it, vi } from "vitest";
import { waitFor } from "@solidjs/testing-library";
import { createSessionStream } from "./sessionStream";

class Source {
  static instances: Source[] = [];
  listeners = new Map<string, EventListener>();
  onopen = null;
  onerror = null;
  constructor() { Source.instances.push(this); }
  addEventListener(type: string, callback: EventListener) { this.listeners.set(type, callback); }
  close() {}
  emit(type: string, data: unknown) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
}
afterEach(() => { vi.unstubAllGlobals(); Source.instances = []; });

it("restores the full live response on return, protects it from old replay, and replaces it on completion", async () => {
  vi.stubGlobal("EventSource", Source);
  let dispose!: () => void;
  const scope = createRoot(cleanup => {
    dispose = cleanup;
    const [id, setId] = createSignal("a");
    return { stream: createSessionStream(id), setId };
  });
  const snapshot = (source: Source, text: string) => source.emit("response.snapshot", {
    durable: false, session_id: "a", run_id: "run", type: "response.snapshot",
    payload: { text, reasoning: "", after_sequence: 10 },
  });
  const durable = (source: Source, sequence: number, content: string) => source.emit("assistant.message", {
    durable: true,
    event: { id: String(sequence), session_id: "a", run_id: "run", sequence,
      type: "assistant.message", payload: { content, status: "completed" } },
  });
  try {
    await waitFor(() => expect(Source.instances).toHaveLength(1));
    snapshot(Source.instances[0]!, "## Full prefix");
    await waitFor(() => expect(scope.stream.liveOutput()?.text).toBe("## Full prefix"));
    scope.setId("b");
    await waitFor(() => expect(Source.instances).toHaveLength(2));
    scope.setId("a");
    await waitFor(() => expect(Source.instances).toHaveLength(3));
    const source = Source.instances[2]!;
    snapshot(source, "## Full prefix while away");
    durable(source, 9, "Previous response in the same run");
    source.emit("response.text_delta", {
      durable: false, session_id: "a", run_id: "run", type: "response.text_delta",
      payload: { text: " and continued" },
    });
    await waitFor(() => expect(scope.stream.liveOutput()?.text).toBe("## Full prefix while away and continued"));
    durable(source, 11, "## Full prefix while away and continued");
    await waitFor(() => expect(scope.stream.liveOutput()?.text).toBe(""));
    // A repeated durable event must not erase a later response in the same run.
    source.emit("response.text_delta", {
      durable: false, session_id: "a", run_id: "run", type: "response.text_delta",
      payload: { text: "Next response" },
    });
    durable(source, 11, "## Full prefix while away and continued");
    await waitFor(() => expect(scope.stream.liveOutput()?.text).toBe("Next response"));
  } finally { dispose(); }
});
