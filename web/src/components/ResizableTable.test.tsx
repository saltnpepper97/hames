import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { ResizableTable } from "./ResizableTable";

describe("ResizableTable", () => {
  it("resizes its column divider with the keyboard and respects both minimums", () => {
    render(() => (
      <ResizableTable
        ariaLabel="Example events"
        defaultFirstColumn={160}
        minFirstColumn={96}
        minSecondColumn={180}
      >
        <div role="row">Example row</div>
      </ResizableTable>
    ));
    const table = screen.getByRole("table", { name: "Example events" });
    Object.defineProperty(table, "clientWidth", { configurable: true, value: 600 });
    fireEvent(window, new Event("resize"));
    const divider = screen.getByRole("separator", { name: "Resize Event and Content columns" });

    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(divider).toHaveAttribute("aria-valuenow", "172");
    expect(table).toHaveStyle("--resizable-first-column: 172px");

    fireEvent.keyDown(divider, { key: "End" });
    expect(divider).toHaveAttribute("aria-valuenow", "420");
    fireEvent.keyDown(divider, { key: "Home" });
    expect(divider).toHaveAttribute("aria-valuenow", "96");
    fireEvent.dblClick(divider);
    expect(divider).toHaveAttribute("aria-valuenow", "160");

    fireEvent.pointerDown(divider, { pointerId: 7, clientX: 160 });
    fireEvent.pointerMove(divider, { pointerId: 7, clientX: 230 });
    expect(divider).toHaveAttribute("aria-valuenow", "230");
    fireEvent.pointerUp(divider, { pointerId: 7, clientX: 230 });
  });
});
