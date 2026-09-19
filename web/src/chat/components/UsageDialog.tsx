import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { getPooledUsage } from "../../api/client";
import type { AccountUsageWindow, DailyUsage, SessionUsage } from "../../api/types";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { LoadingState } from "../../components/LoadingState";
import { ProgressBar } from "../../components/ProgressBar";
import type { ProgressBarVariant } from "../../components/ProgressBar";

interface HeatmapDay {
  date: string;
  future: boolean;
  usage?: DailyUsage;
}

const DAY_MS = 86_400_000;
const HEATMAP_DAYS = 365;

function formatTokens(value: number): string {
  if (value < 1_000) return value.toLocaleString();
  if (value < 1_000_000) return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(value < 10_000_000 ? 1 : 0)}m`;
}

function utcDateKey(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function formatActivityDate(date: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T00:00:00Z`));
}

function planLabel(value: string | null | undefined): string {
  if (!value) return "Subscription";
  const labels: Record<string, string> = {
    free: "Free",
    go: "Go",
    plus: "Plus",
    pro: "Pro",
    prolite: "Pro (Lite)",
    self_serve_business_prolite: "Pro (Lite)",
    promax: "Pro (Max)",
    team: "Team",
    business: "Business",
    self_serve_business: "Business",
    self_serve_business_usage_based: "Business",
    enterprise: "Enterprise",
    enterprise_cbp: "Enterprise",
    enterprise_cbp_usage_based: "Enterprise",
    edu: "Education",
  };
  return labels[value] ?? value;
}

function formatReset(value: number | string | null): string {
  if (value === null) return "Reset time unavailable";
  const parsed = typeof value === "number"
    ? new Date(value < 1_000_000_000_000 ? value * 1_000 : value)
    : new Date(value);
  if (!Number.isFinite(parsed.getTime())) return "Reset time unavailable";
  const remaining = parsed.getTime() - Date.now();
  if (remaining > 0) {
    const minutes = Math.max(1, Math.ceil(remaining / 60_000));
    if (minutes >= 1_440) return `Resets in ${Math.floor(minutes / 1_440)}d ${Math.floor((minutes % 1_440) / 60)}h`;
    if (minutes >= 60) return `Resets in ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    return `Resets in ${minutes}m`;
  }
  return `Reset ${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(parsed)}`;
}

function progressVariant(value: number): ProgressBarVariant {
  if (value >= 90) return "error";
  if (value >= 70) return "warning";
  return "accent";
}

function tokenTotal(usage: DailyUsage): number {
  return usage.input_tokens + usage.output_tokens;
}

function heatmapWeeks(activity: readonly DailyUsage[]): HeatmapDay[][] {
  const byDate = new Map(activity.map((usage) => [usage.date, usage]));
  const now = new Date();
  const today = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const first = new Date(today.getTime() - (HEATMAP_DAYS - 1) * DAY_MS);
  const start = new Date(first.getTime() - first.getUTCDay() * DAY_MS);
  const count = Math.ceil((HEATMAP_DAYS + first.getUTCDay()) / 7);
  return Array.from({ length: count }, (_, week) =>
    Array.from({ length: 7 }, (_, day) => {
      const current = new Date(start.getTime() + (week * 7 + day) * DAY_MS);
      const date = utcDateKey(current);
      return { date, future: current.getTime() > today.getTime() || current.getTime() < first.getTime(), usage: byDate.get(date) };
    })
  );
}

function monthLabel(week: readonly HeatmapDay[], index: number): string {
  const first = week.find(day => day.date.endsWith("-01") && !day.future);
  const last = week.at(-1)?.date;
  // Omit a short leading month rather than colliding with the next label.
  const date = first?.date ?? (index === 0 && last && Number(last.slice(-2)) < 22 ? last : undefined);
  return date ? new Intl.DateTimeFormat(undefined, { month: "short", timeZone: "UTC" })
    .format(new Date(`${date}T00:00:00Z`)) : "";
}

function TokenHeatmap(props: { activity: readonly DailyUsage[] }) {
  const weeks = createMemo(() => heatmapWeeks(props.activity));
  const maximum = createMemo(() => Math.max(0, ...props.activity.map(tokenTotal)));
  const recentTotal = createMemo(() => props.activity.reduce((sum, item) => sum + tokenTotal(item), 0));
  const activeDays = createMemo(() => props.activity.filter((item) => tokenTotal(item) > 0).length);
  const level = (day: HeatmapDay): number => {
    const tokens = day.usage ? tokenTotal(day.usage) : 0;
    if (!tokens || !maximum()) return 0;
    return Math.max(1, Math.ceil((Math.log1p(tokens) / Math.log1p(maximum())) * 4));
  };
  const dayLabel = (day: HeatmapDay): string => {
    const tokens = day.usage ? tokenTotal(day.usage) : 0;
    return `${formatActivityDate(day.date)}: ${tokens ? `${tokens.toLocaleString()} tokens` : "No token activity"}`;
  };

  return (
    <section class="usage-section usage-activity" aria-labelledby="usage-activity-title">
      <div class="usage-section-heading">
        <div>
          <span class="eyebrow">All conversations</span>
          <h3 id="usage-activity-title">Tokens over the past year</h3>
        </div>
        <span class="usage-activity-summary">
          {formatTokens(recentTotal())} tokens · {activeDays()} active {activeDays() === 1 ? "day" : "days"}
        </span>
      </div>
      <div class="usage-heatmap-scroller">
        <div class="usage-heatmap-layout">
          <div class="usage-heatmap-days" aria-hidden="true">
            <span />
            <span>Mon</span>
            <span />
            <span>Wed</span>
            <span />
            <span>Fri</span>
            <span />
          </div>
          <div class="usage-heatmap-calendar">
            <div class="usage-heatmap-months" aria-label="Months">
              <For each={weeks()}>{(week, index) => <span>{monthLabel(week, index())}</span>}</For>
            </div>
            <div class="usage-heatmap-weeks" aria-label="Daily pooled token activity">
              <For each={weeks()}>{(week) => (
                <div class="usage-heatmap-week">
                  <For each={week}>{(day) => (
                    <span
                      class="usage-heatmap-cell"
                      data-level={day.future ? "future" : level(day)}
                      aria-label={day.future ? undefined : dayLabel(day)}
                      title={day.future ? undefined : dayLabel(day)}
                      tabIndex={day.future ? undefined : 0}
                    />
                  )}</For>
                </div>
              )}</For>
            </div>
          </div>
        </div>
      </div>
      <div class="usage-heatmap-legend" aria-hidden="true">
        <span>Less</span>
        <For each={[0, 1, 2, 3, 4]}>{(item) => <i data-level={item} />}</For>
        <span>More</span>
      </div>
    </section>
  );
}

function AccountWindow(props: { label: string; window: AccountUsageWindow }) {
  return (
    <div class="usage-limit">
      <ProgressBar
        label={props.label}
        value={props.window.used}
        showValue
        size="lg"
        variant={progressVariant(props.window.used)}
        formatValue={(value) => `${Math.floor(value)}% used`}
      />
      <span>{formatReset(props.window.reset_at)}</span>
    </div>
  );
}

export function UsageDashboard() {
  const [usage, setUsage] = createSignal<SessionUsage>();
  const [loading, setLoading] = createSignal(true);
  const [error, setError] = createSignal("");
  const [updatedAt, setUpdatedAt] = createSignal<Date>();
  let requestId = 0;

  const load = async () => {
    const currentRequest = ++requestId;
    setLoading(true);
    setError("");
    try {
      const next = await getPooledUsage();
      if (currentRequest !== requestId) return;
      setUsage(next);
      setUpdatedAt(new Date());
    } catch (caught) {
      if (currentRequest === requestId) {
        setError(caught instanceof Error ? caught.message : "Hames could not load usage.");
      }
    } finally {
      if (currentRequest === requestId) setLoading(false);
    }
  };

  createEffect(() => { void load(); });
  onCleanup(() => { requestId += 1; });

  const account = () => usage()?.account_rate_limits;
  const accountWindows = createMemo(() => {
    const current = account();
    if (!current) return [];
    const windows: { label: string; window: AccountUsageWindow }[] = [];
    if (current.sliding_window_5h) windows.push({ label: "5-hour limit", window: current.sliding_window_5h });
    else if (current.primary) windows.push({ label: "Primary limit", window: current.primary });
    if (current.weekly_window) windows.push({ label: "Weekly limit", window: current.weekly_window });
    else if (current.secondary) windows.push({ label: "Secondary limit", window: current.secondary });
    return windows;
  });

  return (
    <div class="usage-dashboard">
      <div class="usage-dashboard-toolbar">
        <div>
          <span class="eyebrow">Pooled locally</span>
          <strong>All chats · all workspaces</strong>
        </div>
        <div class="usage-dashboard-actions">
          <Show when={updatedAt()}>
            {(date) => <span class="usage-updated">Updated {date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>}
          </Show>
          <Button variant="ghost" loading={loading() && Boolean(usage())} onClick={() => void load()}>
            Refresh
          </Button>
        </div>
      </div>
      <div class="usage-dialog-body">
        <Show when={!loading() || usage()} fallback={<LoadingState label="Loading usage" variant="detail" />}>
          <Show when={usage()} fallback={
            <div class="usage-empty-state" role="alert">
              <strong>Usage is unavailable</strong>
              <p>{error() || "Hames could not read this session's usage."}</p>
              <Button onClick={() => void load()}>Try again</Button>
            </div>
          }>
            {(currentUsage) => (
              <>
                <Show when={error()}>
                  <p class="usage-inline-error" role="status">{error()}</p>
                </Show>

                <Show when={accountWindows().length > 0} fallback={
                  <section class="usage-section usage-account-error">
                    <span class="eyebrow">ChatGPT usage</span>
                    <p>{currentUsage().account_rate_limits_error || "Connect a Codex / ChatGPT provider to see account limits."}</p>
                  </section>
                }>
                  <section class="usage-section" aria-labelledby="usage-account-title">
                    <div class="usage-section-heading">
                      <div>
                        <span class="eyebrow">Account</span>
                        <h3 id="usage-account-title">ChatGPT usage</h3>
                      </div>
                      <Badge variant="outline">{planLabel(account()?.plan_type)}</Badge>
                    </div>
                    <div class="usage-limits">
                      <For each={accountWindows()}>{(item) => <AccountWindow {...item} />}</For>
                    </div>
                  </section>
                </Show>

                <Show when={currentUsage().grok_account_configured}>
                  <section class="usage-section" aria-labelledby="usage-grok-title">
                    <div class="usage-section-heading">
                      <div>
                        <span class="eyebrow">Account</span>
                        <h3 id="usage-grok-title">Grok usage</h3>
                      </div>
                    </div>
                    <Show when={currentUsage().grok_account_usage} fallback={
                      <p>{currentUsage().grok_account_usage_error || "Grok did not provide account limits."}</p>
                    }>
                      {(account) => <div class="usage-limits"><AccountWindow label={account().label} window={account().window} /></div>}
                    </Show>
                  </section>
                </Show>

                <TokenHeatmap activity={currentUsage().daily_activity ?? []} />
              </>
            )}
          </Show>
        </Show>
      </div>
    </div>
  );
}
