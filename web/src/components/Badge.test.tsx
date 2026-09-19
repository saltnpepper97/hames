import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { Badge } from "./Badge";

describe("Badge", () => {
  it("exposes the Still-style variant, size, and optional start slot", () => {
    render(() => (
      <Badge variant="warning" size="sm" start={<span data-testid="badge-dot">•</span>}>
        Needs attention
      </Badge>
    ));

    const badge = screen.getByText("Needs attention").closest('[data-component="badge"]');
    expect(badge).toHaveAttribute("data-variant", "warning");
    expect(badge).toHaveAttribute("data-size", "sm");
    expect(screen.getByTestId("badge-dot").parentElement).toHaveAttribute("data-slot", "start");
  });
});
