import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { Spinner } from "./Spinner";

describe("Spinner", () => {
  it("ports the Still UI rounded-square spinner exactly", () => {
    const { container } = render(() => <Spinner />);
    const root = container.querySelector('[data-component="spinner"]');
    const group = container.querySelector("g");
    const rects = container.querySelectorAll("rect");

    expect(root).toHaveAttribute("data-size", "md");
    expect(root).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 24 24");
    expect(group).toHaveAttribute("transform", "rotate(45 12 12)");
    expect(rects).toHaveLength(2);
    for (const rect of rects) {
      expect(rect).toHaveAttribute("x", "4");
      expect(rect).toHaveAttribute("y", "4");
      expect(rect).toHaveAttribute("width", "16");
      expect(rect).toHaveAttribute("height", "16");
      expect(rect).toHaveAttribute("rx", "5");
    }
  });

  it("becomes a named status when labelled and supports custom sizes", () => {
    const { container } = render(() => <Spinner size={18} label="Loading chat" />);
    expect(screen.getByRole("status", { name: "Loading chat" })).toBeInTheDocument();
    expect(container.querySelector('[data-component="spinner"]')).toHaveAttribute("data-size", "custom");
    expect(container.querySelector('[data-component="spinner"]')).toHaveStyle({
      "--_still-spinner-custom-size": "18px",
    });
  });
});
