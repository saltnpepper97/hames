import DOMPurify from "dompurify";
import { marked } from "marked";
import { createMemo } from "solid-js";

interface MarkdownProps {
  content: string;
  class?: string;
  live?: boolean;
}

const allowedTags = [
  "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6",
  "hr", "li", "ol", "p", "pre", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
];

export function renderMarkdown(content: string): string {
  const parsed = marked.parse(content, { async: false, gfm: true, breaks: false });
  return DOMPurify.sanitize(parsed, {
    ALLOWED_TAGS: allowedTags,
    ALLOWED_ATTR: ["class", "href", "start", "title"],
    SANITIZE_NAMED_PROPS: true,
    RETURN_TRUSTED_TYPE: false,
  });
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
