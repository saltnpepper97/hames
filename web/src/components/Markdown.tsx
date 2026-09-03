import DOMPurify from "dompurify";
import { marked } from "marked";
import { createMemo } from "solid-js";

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
    ALLOWED_TAGS: allowedTags,
    ALLOWED_ATTR: ["class", "href", "start", "title"],
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
  const html = createMemo(() => renderMarkdown(props.content));
  return (
    <div
      class={`markdown ${props.class ?? ""}`}
      classList={{ streaming: props.live }}
      aria-busy={props.live || undefined}
      innerHTML={html()}
    />
  );
}

export function MarkdownInline(props: MarkdownInlineProps) {
  const html = createMemo(() => renderMarkdownInline(props.content));
  return <span class={`markdown-inline ${props.class ?? ""}`} innerHTML={html()} />;
}
