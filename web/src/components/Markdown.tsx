import DOMPurify from "dompurify";
import { marked } from "marked";
import { createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";

interface MarkdownProps {
  content: string;
  class?: string;
  live?: boolean;
}

interface MarkdownInlineProps {
  content: string;
  class?: string;
}

const allowedTags = [
  "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6",
  "hr", "li", "ol", "p", "pre", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
];

export function normalizeMarkdownEmphasis(content: string): string {
  let normalized = "";
  let codeDelimiter = 0;

  for (let index = 0; index < content.length;) {
    if (content[index] === "`") {
      let end = index;
      while (content[end] === "`") end += 1;
      const delimiter = end - index;
      if (codeDelimiter === 0) codeDelimiter = delimiter;
      else if (codeDelimiter === delimiter) codeDelimiter = 0;
      normalized += content.slice(index, end);
      index = end;
      continue;
    }

    if (
      codeDelimiter === 0
      && content.slice(index, index + 4) === "****"
      && index > 0
      && index + 4 < content.length
      && !/\s/.test(content[index - 1] ?? "")
      && !/\s/.test(content[index + 4] ?? "")
    ) {
      normalized += "** **";
      index += 4;
      continue;
    }

    normalized += content[index];
    index += 1;
  }

  return normalized;
}

function sanitizedMarkdown(content: string, inline: boolean): string {
  const source = normalizeMarkdownEmphasis(content);
  const parsed = inline
    ? marked.parseInline(source, { async: false, gfm: true, breaks: false })
    : marked.parse(source, { async: false, gfm: true, breaks: false });
  return DOMPurify.sanitize(parsed, {
    ALLOWED_TAGS: inline ? ["a", "code", "del", "em", "strong"] : allowedTags,
    ALLOWED_ATTR: inline ? ["href", "title"] : ["class", "href", "start", "title"],
    SANITIZE_NAMED_PROPS: true,
    RETURN_TRUSTED_TYPE: false,
  });
}

export function renderMarkdown(content: string): string {
  return sanitizedMarkdown(content, false);
}

export function renderMarkdownInline(content: string): string {
  return sanitizedMarkdown(content, true);
}

export function Markdown(props: MarkdownProps) {
  let root!: HTMLDivElement;
  const html = createMemo(() => {
    const rendered = renderMarkdown(props.content);
    const template = document.createElement("template");
    template.innerHTML = rendered;
    for (const table of template.content.querySelectorAll("table")) {
      const scroll = document.createElement("div");
      scroll.className = "markdown-table-scroll";
      scroll.setAttribute("role", "region");
      scroll.setAttribute("aria-label", "Table");
      scroll.tabIndex = 0;
      table.replaceWith(scroll);
      scroll.append(table);
    }
    if (!props.live) return template.innerHTML;
    const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
    let last: Node | undefined;
    while (walker.nextNode()) {
      if (walker.currentNode.textContent?.trim()) last = walker.currentNode;
    }
    if (last) {
      // Markdown blocks must keep the typing mark beside their final text,
      // rather than after the container on a separate block line.
      const caret = document.createElement("span");
      caret.className = "streaming-caret";
      caret.setAttribute("aria-hidden", "true");
      last.parentNode?.insertBefore(caret, last.nextSibling);
    }
    return template.innerHTML;
  });
  const [displayedHtml, setDisplayedHtml] = createSignal("");
  const updateDisplay = () => {
    const next = html();
    const selection = window.getSelection();
    if (root && selection && !selection.isCollapsed &&
      (root.contains(selection.anchorNode) || root.contains(selection.focusNode))) return;
    setDisplayedHtml(next);
  };
  createEffect(updateDisplay);
  onMount(() => document.addEventListener("selectionchange", updateDisplay));
  onCleanup(() => document.removeEventListener("selectionchange", updateDisplay));
  return (
    <div
      class={`markdown ${props.class ?? ""}`}
      classList={{ streaming: props.live }}
      aria-busy={props.live || undefined}
      ref={root}
      innerHTML={displayedHtml()}
    />
  );
}

export function MarkdownInline(props: MarkdownInlineProps) {
  const html = createMemo(() => renderMarkdownInline(props.content));
  return <span class={`markdown-inline ${props.class ?? ""}`} innerHTML={html()} />;
}
