import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageQueue } from "./MessageQueue";
import type { MessageQueueState } from "../../api/types";
import * as api from "../../api/client";

vi.mock("../../api/client", () => ({ editQueuedMessage: vi.fn(), getMessageQueue: vi.fn(), removeQueuedMessage: vi.fn(), sendQueuedMessageNow: vi.fn(), resumeMessageQueue: vi.fn() }));
const queue = (session_id = "one"): MessageQueueState => ({ session_id, paused: false, items: [1, 2, 3].map(position => ({ id: `q${position}`, position, content: `Message ${position}`, attachments: [] })) });
afterEach(() => vi.resetAllMocks());
describe("pending message queue", () => {
  it("shows capacity and order, removes and sends existing messages, and hides when empty", async () => {
    let value = queue();
    vi.mocked(api.getMessageQueue).mockImplementation(async () => value);
    vi.mocked(api.removeQueuedMessage).mockImplementation(async () => { value = { ...value, items: value.items.slice(1) }; return value; });
    vi.mocked(api.sendQueuedMessageNow).mockImplementation(async () => { value = { ...value, items: [] }; return { disposition: "started", run_id: "run", queued: null } as Awaited<ReturnType<typeof api.sendQueuedMessageNow>>; });
    render(() => <MessageQueue sessionId="one" revision="initial" />);
    expect(await screen.findByRole("region")).toHaveTextContent("Queued 3/3");
    expect(screen.getByText("Full")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map(item => item.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("Message 1"), expect.stringContaining("Message 3")]));
    fireEvent.click(screen.getByRole("button", { name: "Delete queued message 1" }));
    await waitFor(() => expect(screen.getByRole("region")).toHaveTextContent("Queued 2/3"));
    expect(api.removeQueuedMessage).toHaveBeenCalledWith("one", "q1");
    fireEvent.click(screen.getByRole("button", { name: "Steer with queued message 2" }));
    await waitFor(() => expect(screen.queryByRole("region")).not.toBeInTheDocument());
    expect(api.sendQueuedMessageNow).toHaveBeenCalledWith("one", "q2");
  });
  it("refreshes after queue events, shows paused state, and keeps failed actions reviewable", async () => {
    vi.mocked(api.getMessageQueue).mockResolvedValue({ ...queue(), items: [] });
    const [revision, setRevision] = createSignal("initial");
    render(() => <MessageQueue sessionId="one" revision={revision()} />);
    await waitFor(() => expect(api.getMessageQueue).toHaveBeenCalledTimes(1));
    vi.mocked(api.getMessageQueue).mockResolvedValue({ ...queue(), paused: true });
    setRevision("queue.enqueued");
    await waitFor(() => expect(screen.getByRole("region")).toHaveTextContent("Queued 3/3 · Paused"));
    vi.mocked(api.resumeMessageQueue).mockRejectedValue(new Error("Please retry"));
    fireEvent.click(screen.getByRole("button", { name: "Resume queue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Please retry");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
  it("ignores a previous chat's late response", async () => {
    let finish!: (value: MessageQueueState) => void;
    vi.mocked(api.getMessageQueue).mockImplementation(id => id === "one" ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ ...queue("two"), items: [] }));
    const [id, setId] = createSignal("one");
    render(() => <MessageQueue sessionId={id()} revision="" />);
    setId("two");
    finish(queue());
    await waitFor(() => expect(api.getMessageQueue).toHaveBeenCalledWith("two"));
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
  it("edits in place with save/cancel while leaving the main composer draft alone", async () => {
    let value = queue();
    vi.mocked(api.getMessageQueue).mockImplementation(async () => value);
    vi.mocked(api.editQueuedMessage).mockImplementation(async (_session, id, content) => {
      value = { ...value, items: value.items.map(item => item.id === id ? { ...item, content } : item) };
      return value;
    });
    render(() => <><textarea aria-label="Main composer" value="Unrelated draft" /><MessageQueue sessionId="one" revision="" /></>);
    fireEvent.input(screen.getByLabelText("Main composer"), { target: { value: "Unrelated draft" } });
    fireEvent.click(await screen.findByRole("button", { name: "Edit queued message 2" }));
    fireEvent.input(screen.getByRole("textbox", { name: "Edit queued message 2" }), { target: { value: "Discard this edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.editQueuedMessage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Edit queued message 2" }));
    expect(screen.getByRole("textbox", { name: "Edit queued message 2" })).toHaveValue("Message 2");
    fireEvent.input(screen.getByRole("textbox", { name: "Edit queued message 2" }), { target: { value: "Revised second message" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Revised second message")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument());
    expect(api.editQueuedMessage).toHaveBeenCalledWith("one", "q2", "Revised second message", "Message 2");
    expect(api.sendQueuedMessageNow).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Main composer")).toHaveValue("Unrelated draft");
  });
  it("retains the edit if its message starts or is removed and prevents saving it", async () => {
    vi.mocked(api.getMessageQueue).mockResolvedValue(queue());
    const [revision, setRevision] = createSignal("");
    render(() => <MessageQueue sessionId="one" revision={revision()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit queued message 1" }));
    fireEvent.input(screen.getByRole("textbox", { name: "Edit queued message 1" }), { target: { value: "Unsent edit" } });
    vi.mocked(api.getMessageQueue).mockResolvedValue({ ...queue(), items: [] });
    setRevision("queue.promoted");
    expect(await screen.findByRole("status")).toHaveTextContent("already started or was removed");
    expect(screen.getByRole("textbox", { name: "Edit queued message 1" })).toHaveValue("Unsent edit");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
  it("keeps rejected edits for correction and never sends a replacement", async () => {
    vi.mocked(api.getMessageQueue).mockResolvedValue(queue());
    vi.mocked(api.editQueuedMessage).mockRejectedValue(new Error("Message changed elsewhere"));
    render(() => <MessageQueue sessionId="one" revision="" />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit queued message 1" }));
    fireEvent.input(screen.getByRole("textbox", { name: "Edit queued message 1" }), { target: { value: "Keep this edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Message changed elsewhere");
    expect(screen.getByRole("textbox", { name: "Edit queued message 1" })).toHaveValue("Keep this edit");
    expect(api.sendQueuedMessageNow).not.toHaveBeenCalled();
  });

});
