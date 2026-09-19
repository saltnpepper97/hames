import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { SelectField, TextAreaField, TextField } from "./FormField";

describe("shared form fields", () => {
  it("renders a labeled custom select and changes its value", () => {
    const onValueChange = vi.fn();
    const { container } = render(() => (
      <SelectField
        label="Severity"
        value="medium"
        options={[
          { value: "low", label: "Low" },
          { value: "medium", label: "Medium" },
          { value: "high", label: "High" },
        ]}
        onValueChange={onValueChange}
      />
    ));

    const select = screen.getByRole("combobox", { name: "Severity" });
    expect(select).toHaveClass("select-trigger");
    expect(container.querySelector(".select-control-icon")).toBeInTheDocument();
    fireEvent.click(select);
    const listbox = screen.getByRole("listbox", { name: "Severity" });
    expect(listbox).toBeInTheDocument();
    expect(container).not.toContainElement(listbox);
    fireEvent.click(screen.getByRole("option", { name: "High" }));
    expect(onValueChange).toHaveBeenCalledWith("high");
  });

  it("supports wraparound keyboard navigation and selection", () => {
    const onValueChange = vi.fn();
    render(() => (
      <SelectField
        label="Scope"
        value="workspace"
        options={[
          { value: "workspace", label: "Workspace" },
          { value: "agent", label: "Agent" },
          { value: "global", label: "Global" },
        ]}
        onValueChange={onValueChange}
      />
    ));

    const trigger = screen.getByRole("combobox", { name: "Scope" });
    fireEvent.keyDown(trigger, { key: "ArrowUp" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("option", { name: "Workspace" })).toHaveAttribute("data-active");
    fireEvent.keyDown(trigger, { key: "ArrowUp" });
    expect(screen.getByRole("option", { name: "Global" })).toHaveAttribute("data-active");
    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(onValueChange).toHaveBeenCalledWith("global");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("uses the shared input error and description wiring", () => {
    render(() => <TextField label="Name" error="A name is required." />);

    const input = screen.getByRole("textbox", { name: "Name" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("A name is required.");
  });

  it("adds the accessible custom grip only when resizing is useful", () => {
    const { unmount } = render(() => <TextAreaField label="Instructions" />);

    expect(screen.getByRole("button", { name: "Resize Instructions" })).toBeInTheDocument();
    unmount();

    render(() => <TextAreaField label="What happened?" resizable={false} />);
    expect(screen.queryByRole("button", { name: "Resize What happened?" })).not.toBeInTheDocument();
  });
});
