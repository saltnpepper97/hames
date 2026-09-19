import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { expect, it, vi } from "vitest";
import { hamesIconPack } from "../plugins/icons/hames";
import { IconProvider } from "../shell/icons";
import { DeletableSidebarRow } from "./DeletableSidebarRow";

it("keeps a failed deletion retryable and blocks duplicate pending requests", async () => {
  let reject!: (reason: Error) => void;
  const remove = vi.fn().mockImplementationOnce(() => new Promise((_, no) => { reject = no; })).mockResolvedValue(undefined);
  render(() => <IconProvider pack={hamesIconPack}><DeletableSidebarRow name="Example" kind="memory"
    description={<p>Delete this memory.</p>} onDelete={remove}><a href="/memory/example">Example</a></DeletableSidebarRow></IconProvider>);
  const trigger = screen.getByRole("button", {name:"Delete Example"});
  expect(trigger.closest("a")).toBeNull();
  fireEvent.click(trigger);
  const confirm = screen.getByRole("button", {name:"Delete memory"});
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(remove).toHaveBeenCalledTimes(1);
  expect(confirm).toBeDisabled();
  reject(new Error("Still in use"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Still in use");
  expect(screen.getByRole("link", {name:"Example"})).toBeInTheDocument();
  fireEvent.click(confirm);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(remove).toHaveBeenCalledTimes(2);
});
