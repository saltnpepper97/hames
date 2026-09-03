import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { Switch } from "./Switch";

describe("Switch", () => {
  it("uses native checkbox behavior with switch semantics", () => {
    const onCheckedChange = vi.fn();
    render(() => <Switch label="Dark mode" checked={false} onCheckedChange={onCheckedChange} />);

    const toggle = screen.getByRole("switch", { name: "Dark mode" });
    expect(toggle).toHaveAttribute("type", "checkbox");
    fireEvent.click(toggle);
    expect(onCheckedChange).toHaveBeenCalledWith(true, expect.any(Event));
  });

  it("associates descriptions and exposes its visual state", () => {
    render(() => <Switch label="Dark mode" description="Use a darker palette" checked />);

    const toggle = screen.getByRole("switch", { name: "Dark mode" });
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAccessibleDescription("Use a darker palette");
    expect(toggle.closest("label")).toHaveAttribute("data-checked");
  });

  it("supports disabled and label-position states", () => {
    render(() => <Switch label="Unavailable" labelPosition="start" disabled />);

    const toggle = screen.getByRole("switch", { name: "Unavailable" });
    expect(toggle).toBeDisabled();
    expect(toggle.closest("label")).toHaveAttribute("data-disabled");
    expect(toggle.closest("label")).toHaveAttribute("data-label-position", "start");
  });
});
