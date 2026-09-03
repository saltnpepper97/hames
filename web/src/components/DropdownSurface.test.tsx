import { fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { describe, expect, it } from "vitest";
import { DropdownSurface } from "./DropdownSurface";

describe("DropdownSurface", () => {
  it("keeps closing content present until its exit animation completes", async () => {
    function Fixture() {
      const [open, setOpen] = createSignal(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open</button>
          <button onClick={() => setOpen(false)}>Close</button>
          <DropdownSurface open={open()} class="test-menu" role="menu" ariaLabel="Test menu">
            <button role="menuitem">Choice</button>
          </DropdownSurface>
        </>
      );
    }

    render(() => <Fixture />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByRole("menu", { name: "Test menu" })).toHaveAttribute("data-state", "open");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(document.querySelector(".test-menu")).toHaveAttribute("data-state", "closed");
    expect(document.querySelector(".test-menu")).toHaveAttribute("aria-hidden", "true");

    await waitFor(() => expect(document.querySelector(".test-menu")).not.toBeInTheDocument());
  });
});
