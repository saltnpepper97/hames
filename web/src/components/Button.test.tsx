import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";

describe("Button", () => {
  it("provides a safe default type and forwards interactions", () => {
    const onClick = vi.fn();
    render(() => <Button variant="primary" onClick={onClick}>Save</Button>);

    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveClass("ui-button-primary");
    expect(button).toHaveAttribute("data-component", "button");
    expect(button).toHaveAttribute("data-variant", "primary");
    expect(button).toHaveAttribute("data-size", "md");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("disables interaction and exposes progress while loading", () => {
    const onClick = vi.fn();
    render(() => <Button loading onClick={onClick}>Saving</Button>);

    const button = screen.getByRole("button", { name: "Saving" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveAttribute("data-loading");
    expect(button.querySelector('[data-component="spinner"]')).toHaveClass("ui-button-spinner");
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("supports the Still-style variants, slots, sizes, and full-width state", () => {
    render(() => (
      <Button
        variant="destructive"
        size="sm"
        fullWidth
        start={<span data-testid="start-slot">!</span>}
        end={<span data-testid="end-slot">→</span>}
      >
        Delete
      </Button>
    ));

    const button = screen.getByRole("button", { name: "!Delete→" });
    expect(button).toHaveClass("ui-button-destructive", "ui-button-sm");
    expect(button).toHaveAttribute("data-full-width");
    expect(screen.getByTestId("start-slot").parentElement).toHaveAttribute("data-slot", "start");
    expect(screen.getByTestId("end-slot").parentElement).toHaveAttribute("data-slot", "end");
  });
});
