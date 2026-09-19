import { Show, createMemo, createUniqueId, splitProps } from "solid-js";
import type { JSX } from "solid-js";

export type ProgressBarSize = "sm" | "md" | "lg";
export type ProgressBarVariant = "accent" | "success" | "warning" | "error" | "neutral";

export interface ProgressBarProps
  extends Omit<JSX.HTMLAttributes<HTMLDivElement>, "children"> {
  value?: number | null;
  min?: number;
  max?: number;
  label: string;
  hideLabel?: boolean;
  showValue?: boolean;
  formatValue?: (value: number, min: number, max: number) => string;
  variant?: ProgressBarVariant;
  size?: ProgressBarSize;
}

function defaultFormatValue(value: number, minimum: number, maximum: number): string {
  if (maximum === minimum) return "0%";
  return `${Math.round(((value - minimum) / (maximum - minimum)) * 100)}%`;
}

export function ProgressBar(props: ProgressBarProps) {
  const [local, progressProps] = splitProps(props, [
    "value",
    "min",
    "max",
    "label",
    "hideLabel",
    "showValue",
    "formatValue",
    "variant",
    "size",
    "id",
    "class",
    "aria-label",
    "aria-labelledby",
    "aria-describedby",
    "aria-valuetext",
  ]);
  const id = local.id ?? `progress-${createUniqueId()}`;
  const labelId = `${id}-label`;
  const indeterminate = () => local.value === null;
  const minimum = createMemo(() => Number.isFinite(local.min) ? Number(local.min) : 0);
  const maximum = createMemo(() => Number.isFinite(local.max) ? Number(local.max) : 100);
  const safeMinimum = createMemo(() => Math.min(minimum(), maximum()));
  const safeMaximum = createMemo(() => Math.max(minimum(), maximum()));
  const safeValue = createMemo(() => {
    const fallback = safeMinimum();
    const current = Number.isFinite(local.value) ? Number(local.value) : fallback;
    return Math.min(Math.max(current, safeMinimum()), safeMaximum());
  });
  const percent = createMemo(() => safeMaximum() === safeMinimum()
    ? 0
    : ((safeValue() - safeMinimum()) / (safeMaximum() - safeMinimum())) * 100
  );
  const formattedValue = createMemo(() => indeterminate()
    ? undefined
    : local["aria-valuetext"] ?? (local.formatValue ?? defaultFormatValue)(
      safeValue(),
      safeMinimum(),
      safeMaximum(),
    )
  );
  const state = () => indeterminate() ? "indeterminate" : percent() >= 100 ? "complete" : "loading";

  return (
    <div
      {...progressProps}
      id={id}
      class={`ui-progress ${local.class ?? ""}`}
      data-component="progress-bar"
      data-state={state()}
      data-variant={local.variant ?? "accent"}
      data-size={local.size ?? "md"}
      data-indeterminate={indeterminate() ? "" : undefined}
    >
      <Show
        when={!local.hideLabel || local.showValue}
        fallback={<span id={labelId} class="ui-progress-label visually-hidden">{local.label}</span>}
      >
        <div class="ui-progress-heading" data-part="heading">
          <span
            id={labelId}
            class="ui-progress-label"
            classList={{ "visually-hidden": Boolean(local.hideLabel) }}
            data-part="label"
          >
            {local.label}
          </span>
          <Show when={local.showValue && !indeterminate()}>
            <span class="ui-progress-value" data-part="value">{formattedValue()}</span>
          </Show>
        </div>
      </Show>
      <div
        class="ui-progress-track"
        role="progressbar"
        aria-label={local["aria-label"]}
        aria-labelledby={local["aria-labelledby"] ?? (local["aria-label"] ? undefined : labelId)}
        aria-describedby={local["aria-describedby"]}
        aria-valuemin={indeterminate() ? undefined : safeMinimum()}
        aria-valuemax={indeterminate() ? undefined : safeMaximum()}
        aria-valuenow={indeterminate() ? undefined : safeValue()}
        aria-valuetext={formattedValue()}
        data-part="track"
        data-state={state()}
      >
        <span
          class="ui-progress-fill"
          style={{ "--progress-percent": `${percent()}%` } as JSX.CSSProperties}
          data-part="fill"
          data-state={state()}
        />
      </div>
    </div>
  );
}
