import { render } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { LoadingState } from "./LoadingState";
import { Skeleton } from "./Skeleton";

describe("Skeleton", () => {
  it("preserves Still UI sizing, radius, and stagger metadata", () => {
    const { container } = render(() => (
      <Skeleton width="62%" height={48} radius="rounded" index={2} />
    ));
    const skeleton = container.querySelector('[data-component="skeleton"]');
    expect(skeleton).toHaveAttribute("aria-hidden", "true");
    expect(skeleton).toHaveAttribute("data-radius", "rounded");
    expect(skeleton).toHaveAttribute("data-index", "2");
    expect(skeleton).toHaveStyle({
      "--_still-skeleton-width": "62%",
      "--_still-skeleton-height": "48px",
      "--_still-skeleton-index-delay": "230ms",
    });
  });

  it("combines a labelled Still spinner with shaped placeholders", () => {
    const { container } = render(() => (
      <LoadingState variant="detail" label="Loading memory" />
    ));
    expect(container.querySelector('[data-component="spinner"]')).toHaveAttribute(
      "aria-label",
      "Loading memory",
    );
    expect(container.querySelectorAll('[data-component="skeleton"]')).toHaveLength(3);
  });
});
