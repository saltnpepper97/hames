import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { getSessionUsage } from "../../api/client";
import type { Session, SessionUsage } from "../../api/types";
import { ProgressBar } from "../../components/ProgressBar";
import { Skeleton } from "../../components/Skeleton";

interface ComposerStatsLineProps {
  session: Session;
  activeRunId?: string;
}

function formatTokens(value: number): string {
  if (value < 1_000) return value.toLocaleString();
  if (value < 1_000_000) {
    const thousands = value / 1_000;
    return `${thousands.toFixed(value < 10_000 && !Number.isInteger(thousands) ? 1 : 0)}k`;
  }
  const millions = value / 1_000_000;
  return `${millions.toFixed(value < 10_000_000 && !Number.isInteger(millions) ? 1 : 0)}m`;
}

function contextPercent(usage: SessionUsage): number | undefined {
  const context = usage.latest_context;
  if (!context || context.context_window_tokens <= 0) return undefined;
  return Math.min(100, Math.max(0, Math.round(
    (context.estimated_input_tokens / context.context_window_tokens) * 100,
  )));
}

function cacheHitPercent(usage: SessionUsage): string | undefined {
  if (usage.input_tokens <= 0) return undefined;
  const percent = Math.min(100, Math.max(0, (usage.cached_input_tokens / usage.input_tokens) * 100));
  if (percent > 99 && percent < 100) return `${Number(percent.toFixed(2))}%`;
  return `${Math.round(percent)}%`;
}

export function ComposerStatsLine(props: ComposerStatsLineProps) {
  const [usage, setUsage] = createSignal<SessionUsage>();
  const [loading, setLoading] = createSignal(true);
  const [open, setOpen] = createSignal(false);
  let root!: HTMLDivElement;
  let requestId = 0;
  let observedSessionId = "";

  createEffect(() => {
    const sessionId = props.session.id;
    const activeRunId = props.activeRunId;
    const sessionChanged = observedSessionId !== sessionId;
    if (!sessionChanged && activeRunId) return;
    observedSessionId = sessionId;
    if (sessionChanged) setUsage(undefined);
    const currentRequest = ++requestId;
    setLoading(true);
    void getSessionUsage(sessionId)
      .then((next) => {
        if (currentRequest === requestId) setUsage(next);
      })
      .catch(() => {
        if (currentRequest === requestId) setUsage(undefined);
      })
      .finally(() => {
        if (currentRequest === requestId) setLoading(false);
      });
  });
  onCleanup(() => { requestId += 1; });

  createEffect(() => {
    if (!open()) return;
    const closeOutside = (event: PointerEvent) => {
      if (!root.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    onCleanup(() => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    });
  });

  const groups = createMemo(() => {
    const current = usage();
    if (!current) return [];
    const values: string[] = [];
    if (current.model_requests > 0) {
      values.push(`${current.model_requests.toLocaleString()} ${current.model_requests === 1 ? "request" : "requests"}`);
    }
    const cacheHit = cacheHitPercent(current);
    if (cacheHit !== undefined) values.push(`Cache hit ${cacheHit}`);
    if (current.input_tokens > 0 || current.output_tokens > 0) {
      values.push(`Input ${formatTokens(current.input_tokens)} · Output ${formatTokens(current.output_tokens)}`);
    }
    const pressure = contextPercent(current);
    if (pressure !== undefined) values.push(`${pressure}% context`);
    return values;
  });
  const label = () => groups().join(" | ");
  const context = () => usage()?.latest_context;
  const contextVariant = () => {
    const percent = usage() ? contextPercent(usage()!) ?? 0 : 0;
    if (percent >= 90) return "error" as const;
    if (percent >= 70) return "warning" as const;
    return "accent" as const;
  };

  return (
    <div class="composer-stats-line" ref={root}>
      <Show
        when={!loading() || usage()}
        fallback={<Skeleton class="composer-stats-skeleton" width="15rem" height="0.65rem" radius="rounded" />}
      >
        <Show when={groups().length > 0}>
          <button
            type="button"
            class="composer-stats-trigger"
            aria-label={`Open token breakdown · ${label()}`}
            aria-haspopup="dialog"
            aria-expanded={open()}
            aria-controls={open() ? "composer-context-breakdown" : undefined}
            title="Open token breakdown"
            onClick={() => setOpen((current) => !current)}
          >
            <For each={groups()}>{(group, index) => (
              <>
                <Show when={index() > 0}><span class="composer-stats-separator">|</span></Show>
                <span>{group}</span>
              </>
            )}</For>
          </button>
        </Show>
      </Show>
      <Show when={open() && usage()}>
        {(currentUsage) => (
          <section
            id="composer-context-breakdown"
            class="composer-context-popover"
            role="dialog"
            aria-label="Token breakdown"
          >
            <div class="composer-context-heading">
              <div>
                <span class="eyebrow">Conversation</span>
                <strong>Token usage</strong>
              </div>
              <span>{props.session.provider} · {props.session.model}</span>
            </div>
            <Show when={context()} fallback={
              <p class="composer-context-empty">Context details appear after the first compiled model request.</p>
            }>
              {(currentContext) => (
                <>
                  <ProgressBar
                    class="composer-context-progress"
                    label="Context window"
                    value={currentContext().estimated_input_tokens}
                    max={currentContext().context_window_tokens}
                    showValue
                    variant={contextVariant()}
                    formatValue={(value, _min, max) => `${formatTokens(value)} / ${formatTokens(max)}`}
                  />
                  <div class="composer-context-budget">
                    <span>{formatTokens(currentContext().input_budget_tokens)} input budget</span>
                    <span>{formatTokens(currentContext().output_reserve_tokens)} response reserve</span>
                  </div>
                </>
              )}
            </Show>
            <dl class="composer-context-grid">
              <div class="composer-context-total">
                <dt>Total tokens</dt>
                <dd>{formatTokens(currentUsage().input_tokens + currentUsage().output_tokens)}</dd>
              </div>
              <div><dt>Requests</dt><dd>{currentUsage().model_requests.toLocaleString()}</dd></div>
              <div><dt>Input / prompt</dt><dd>{formatTokens(currentUsage().input_tokens)}</dd></div>
              <div><dt>Output</dt><dd>{formatTokens(currentUsage().output_tokens)}</dd></div>
              <div>
                <dt>Cached input</dt>
                <dd>
                  {formatTokens(currentUsage().cached_input_tokens)}
                  <Show when={cacheHitPercent(currentUsage())}>{(hit) => ` · ${hit()} hit`}</Show>
                </dd>
              </div>
              <div><dt>Reasoning</dt><dd>{formatTokens(currentUsage().reasoning_tokens)}</dd></div>
              <div><dt>Compiled input</dt><dd>{formatTokens(currentUsage().estimated_input_tokens)}</dd></div>
              <Show when={currentUsage().provider_reported_cost > 0}>
                <div><dt>Reported cost</dt><dd>${currentUsage().provider_reported_cost.toFixed(4)}</dd></div>
              </Show>
            </dl>
          </section>
        )}
      </Show>
    </div>
  );
}
