import { Show, createSignal, onCleanup, onMount } from "solid-js";
import type { JSX } from "solid-js";
import { Button } from "./Button";

interface DetailHeadingProps {
  id: string;
  eyebrow: JSX.Element;
  title: string;
  summary?: string;
  context?: JSX.Element;
  badges?: JSX.Element;
  class?: string;
}

export function DetailHeading(props: DetailHeadingProps) {
  const [summaryExpanded, setSummaryExpanded] = createSignal(false);
  const [summaryCanExpand, setSummaryCanExpand] = createSignal(false);
  const summaryId = () => `${props.id}-summary`;
  let summaryElement: HTMLParagraphElement | undefined;

  const measureSummary = () => {
    if (!summaryElement || summaryExpanded()) return;
    setSummaryCanExpand(summaryElement.scrollHeight > summaryElement.clientHeight + 1);
  };

  const toggleSummary = () => {
    if (summaryExpanded()) {
      setSummaryExpanded(false);
      requestAnimationFrame(measureSummary);
    } else {
      setSummaryExpanded(true);
    }
  };

  onMount(() => {
    measureSummary();
    if (typeof ResizeObserver === "undefined" || !summaryElement) return;
    const observer = new ResizeObserver(measureSummary);
    observer.observe(summaryElement);
    onCleanup(() => observer.disconnect());
  });

  return (
    <header class={`detail-heading ${props.class ?? ""}`}>
      <div class="detail-heading-copy">
        <span class="eyebrow">{props.eyebrow}</span>
        <h1 id={props.id}>{props.title}</h1>
        <Show when={props.summary}>
          {(summary) => (
            <div
              class="detail-heading-summary"
              data-expanded={summaryExpanded() ? "true" : "false"}
            >
              <p id={summaryId()} ref={summaryElement}>{summary()}</p>
              <Show when={summaryCanExpand()}>
                <Button
                  variant="bare"
                  class="detail-heading-summary-toggle"
                  aria-controls={summaryId()}
                  aria-expanded={summaryExpanded()}
                  onClick={toggleSummary}
                >
                  {summaryExpanded() ? "Show less" : "Show more"}
                </Button>
              </Show>
            </div>
          )}
        </Show>
        <Show when={props.context}>
          <div class="detail-heading-context">{props.context}</div>
        </Show>
      </div>
      <Show when={props.badges}>
        <div class="detail-heading-badges">{props.badges}</div>
      </Show>
    </header>
  );
}
