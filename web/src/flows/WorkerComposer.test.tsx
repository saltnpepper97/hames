import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { afterEach, expect, it, vi } from "vitest";
import { WorkerComposer } from "./WorkerComposer";
const send = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ sendMessage: send }));
vi.mock("../shell/icons", () => ({ Icon: () => <span /> }));
vi.mock("../chat/components/MessageQueue", () => ({ MessageQueue: () => null }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function mount() {
  const [draft, setDraft] = createSignal("");
  return render(() => <WorkerComposer sessionId="selected-child" agentName="Builder" revision="1" draft={draft()} onDraft={setDraft} />);
}
it("sends to the selected worker without a success badge and preserves a newer draft", async () => {
  let resolve!: (value: unknown) => void;
  send.mockReturnValue(new Promise(done => { resolve = done; }));
  mount();
  const input = screen.getByRole("textbox", { name: "Message Builder" });
  expect(screen.getByRole("button", { name: "Send to Builder" })).toHaveClass("composer-round", "send");
  fireEvent.input(input, { target: { value: "Keep the existing API" } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(send).toHaveBeenCalledWith("selected-child", "Keep the existing API");
  fireEvent.input(input, { target: { value: "Another clarification" } });
  resolve({ disposition: "queued" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Send to Builder" })).not.toBeDisabled());
  expect(input).toHaveValue("Another clarification");
  expect(screen.queryByText(/Message queued|sent|started/i)).not.toBeInTheDocument();
});
it("clears accepted drafts but keeps failed messages for retry", async () => {
  send.mockRejectedValueOnce(new Error("Connection lost")).mockResolvedValueOnce({ disposition: "started" });
  mount();
  const input = screen.getByRole("textbox", { name: "Message Builder" });
  fireEvent.input(input, { target: { value: "Check the tests" } });
  fireEvent.click(screen.getByRole("button", { name: "Send to Builder" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
  expect(input).toHaveValue("Check the tests");
  fireEvent.click(screen.getByRole("button", { name: "Send to Builder" }));
  await waitFor(() => expect(input).toHaveValue(""));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
