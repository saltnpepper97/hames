import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { ProgressBar } from "./ProgressBar";

describe("ProgressBar", () => {
  it("exposes determinate progress and the Still-style visual contract", () => {
    render(() => <ProgressBar label="Weekly limit" value={42} showValue size="lg" />);

    const progress = screen.getByRole("progressbar", { name: "Weekly limit" });
    expect(progress).toHaveAttribute("aria-valuemin", "0");
    expect(progress).toHaveAttribute("aria-valuemax", "100");
    expect(progress).toHaveAttribute("aria-valuenow", "42");
    expect(progress).toHaveAttribute("aria-valuetext", "42%");
    expect(progress.closest('[data-component="progress-bar"]')).toHaveAttribute("data-size", "lg");
    expect(progress.querySelector('[data-part="fill"]')).toHaveStyle("--progress-percent: 42%");
  });

  it("clamps values to the configured range", () => {
    render(() => <ProgressBar label="Context" value={180} max={120} />);

    const progress = screen.getByRole("progressbar", { name: "Context" });
    expect(progress).toHaveAttribute("aria-valuenow", "120");
    expect(progress.querySelector('[data-part="fill"]')).toHaveStyle("--progress-percent: 100%");
  });

  it("supports an accessible indeterminate state", () => {
    render(() => <ProgressBar label="Refreshing" value={null} />);

    const progress = screen.getByRole("progressbar", { name: "Refreshing" });
    expect(progress).not.toHaveAttribute("aria-valuenow");
    expect(progress).not.toHaveAttribute("aria-valuetext");
    expect(progress.closest('[data-component="progress-bar"]')).toHaveAttribute("data-indeterminate");
  });
});
