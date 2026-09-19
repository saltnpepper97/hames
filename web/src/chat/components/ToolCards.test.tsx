import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import { parseUnifiedDiff } from "../toolPresentation";
import { DiffCard, TerminalCard } from "./ToolCards";

describe("tool transcript cards", () => {
  it("renders a bounded diff with line counts and expansion", () => {
    const additions = Array.from({ length: 14 }, (_, index) => `+line ${index + 1}`);
    const content = [
      "--- /dev/null",
      "+++ b/src/new.ts",
      "@@ -0,0 +1,14 @@",
      ...additions,
    ].join("\n");
    const diff = parseUnifiedDiff(content);
    if (!diff) throw new Error("fixture did not parse");

    render(() => <DiffCard diff={diff} content={content} />);

    expect(screen.getByRole("region", { name: "Changes to src/new.ts" })).toBeInTheDocument();
    expect(screen.getByText("+14")).toBeInTheDocument();
    expect(screen.getByText("−0")).toBeInTheDocument();
    const expand = screen.getByRole("button", { name: /Show 7 more lines/ });
    fireEvent.click(expand);
    expect(screen.getByText("line 7")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Collapse output" })).toBeInTheDocument();
  });

  it("renders command chrome, exit state, and bounded terminal output", () => {
    render(() => (
      <TerminalCard terminal={{
        command: "pnpm test",
        cwd: "/work/hames",
        stdout: Array.from({ length: 13 }, (_, index) => `test ${index + 1}`).join("\n"),
        stderr: "",
        exitCode: 1,
        durationSeconds: 2.4,
        running: false,
        state: "error",
        truncated: false,
      }} />
    ));

    const card = screen.getByRole("region", { name: "Shell command" });
    expect(card).toHaveAttribute("data-state", "error");
    expect(card).toHaveTextContent("pnpm test");
    expect(card).toHaveTextContent("/work/hames");
    expect(card).toHaveTextContent("Exit 1");
    expect(card).toHaveTextContent("2.4s");
    fireEvent.click(screen.getByRole("button", { name: /Show 4 more lines/ }));
    expect(screen.getByText("test 8")).toBeInTheDocument();
  });
});
