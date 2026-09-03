import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { IconProvider } from "../shell/icons";
import { hamesIconPack } from "../plugins/icons/hames";
import { Checkbox } from "./Checkbox";

function renderCheckbox(props: Parameters<typeof Checkbox>[0]) {
  return render(() => (
    <IconProvider pack={hamesIconPack}>
      <Checkbox {...props} />
    </IconProvider>
  ));
}

describe("Checkbox", () => {
  it("keeps native checkbox behavior and forwards checked changes", () => {
    const onCheckedChange = vi.fn();
    renderCheckbox({ label: "Shell", checked: false, onCheckedChange });

    const checkbox = screen.getByRole("checkbox", { name: "Shell" });
    fireEvent.click(checkbox);

    expect(onCheckedChange).toHaveBeenCalledWith(true, expect.any(Event));
    expect(checkbox.closest("label")).toHaveAttribute("data-state", "unchecked");
  });

  it("exposes its indeterminate state to the native input", () => {
    renderCheckbox({ label: "All tools", indeterminate: true });

    const checkbox = screen.getByRole("checkbox", { name: "All tools" }) as HTMLInputElement;
    expect(checkbox.indeterminate).toBe(true);
    expect(checkbox).toHaveAttribute("data-state", "indeterminate");
    expect(checkbox.closest("label")).toHaveAttribute("data-state", "indeterminate");
  });

  it("associates descriptions and preserves disabled state", () => {
    renderCheckbox({ label: "Unavailable", description: "Not granted by policy", disabled: true });

    const checkbox = screen.getByRole("checkbox", { name: "Unavailable" });
    expect(checkbox).toBeDisabled();
    expect(checkbox).toHaveAccessibleDescription("Not granted by policy");
    expect(checkbox.closest("label")).toHaveAttribute("data-disabled");
  });
});
