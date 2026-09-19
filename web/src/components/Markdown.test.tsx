import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it, vi } from "vitest";
import { Markdown, MarkdownInline } from "./Markdown";
import { MarkdownEditor } from "./MarkdownEditor";

describe("Markdown", () => {
  it.each([false, true])("keeps tables semantic inside a keyboard-scrollable region (live=%s)", live => {
    render(() => <Markdown live={live} content={"| Column | A longer heading |\n| --- | --- |\n| value | A readable sentence |"} />);
    const table = screen.getByRole("table");
    expect(table.parentElement).toHaveClass("markdown-table-scroll");
    expect(table.parentElement).toHaveAttribute("tabindex", "0");
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
  });
  it.each(["A wrapped paragraph", "- A final list item", "```js\nvalue\n```", "> A quote"])("places the live caret inside the final text block: %s", content => {
    const { container } = render(() => <Markdown content={content} live />);
    const caret = container.querySelector(".streaming-caret")!;
    expect(caret).toHaveAttribute("aria-hidden", "true");
    expect(caret.parentElement).not.toHaveClass("markdown");
    expect(caret.previousSibling?.nodeType).toBe(Node.TEXT_NODE);
  });
  it("renders GFM structure", () => {
    const { container } = render(() => (
      <Markdown content={"## Heading\n\n- one\n- two\n\n`inline`\n\n```ts\nconst value = 1;\n```"} />
    ));

    expect(screen.getByRole("heading", { name: "Heading", level: 2 })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(container.querySelector("code.language-ts")).toHaveTextContent("const value = 1;");
  });

  it("removes executable markup and remote images", () => {
    const { container } = render(() => (
      <Markdown content={'<script>alert(1)</script>\n\n<img src="https://example.com/pixel.png">\n\n[unsafe](javascript:alert(1))'} />
    ));

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(screen.getByText("unsafe")).not.toHaveAttribute("href");
  });

  it("repairs adjacent bold boundaries without changing code", () => {
    const { container } = render(() => (
      <div>
        <MarkdownInline content={"**Analyzing...****Designing...**"} />
        <Markdown content={"`left****right`"} />
      </div>
    ));

    expect(container.querySelectorAll(".markdown-inline strong")).toHaveLength(2);
    expect(container.querySelector(".markdown-inline")).toHaveTextContent("Analyzing... Designing...");
    expect(container.querySelector(".markdown-inline")).not.toHaveTextContent("**");
    expect(container.querySelector("code")).toHaveTextContent("left****right");
  });
});

describe("MarkdownEditor", () => {
  it("switches between controlled source and rendered preview", () => {
    const onInput = vi.fn();
    render(() => (
      <MarkdownEditor label="Instructions" value={"# Agent\n\nUse **care**."} onInput={onInput} />
    ));

    fireEvent.input(screen.getByRole("textbox", { name: "Instructions" }), {
      target: { value: "changed" },
    });
    expect(onInput).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
    expect(screen.getByRole("heading", { name: "Agent", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("care")).toHaveProperty("tagName", "STRONG");
  });
});

it("keeps selected streaming text intact until selection is released", async () => {
  const { createSignal } = await import("solid-js");
  const [content, setContent] = createSignal("Original selected text");
  const { container } = render(() => <Markdown content={content()} live />);
  const paragraph = container.querySelector("p")!;
  const selection = window.getSelection()!;
  const range = document.createRange();
  range.selectNodeContents(paragraph);
  selection.removeAllRanges();
  selection.addRange(range);
  setContent("Original selected text plus new output");
  expect(selection.toString()).toBe("Original selected text");
  expect(paragraph.isConnected).toBe(true);
  selection.removeAllRanges();
  document.dispatchEvent(new Event("selectionchange"));
  expect(container).toHaveTextContent("Original selected text plus new output");
  setContent("Later output still updates");
  expect(container).toHaveTextContent("Later output still updates");
});
