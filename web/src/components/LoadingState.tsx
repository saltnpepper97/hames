import { For, Show } from "solid-js";
import { Skeleton } from "./Skeleton";
import { Spinner } from "./Spinner";

export type LoadingStateVariant = "inline" | "sidebar" | "detail" | "chat";

interface LoadingStateProps {
  label: string;
  variant?: LoadingStateVariant;
  class?: string;
}

const skeletonCounts: Record<Exclude<LoadingStateVariant, "inline">, number> = {
  sidebar: 3,
  detail: 3,
  chat: 3,
};

export function LoadingState(props: LoadingStateProps) {
  const variant = () => props.variant ?? "inline";
  const skeletonCount = () => {
    const current = variant();
    return current === "inline" ? 0 : skeletonCounts[current];
  };

  return (
    <div
      class={`loading-state loading-state-${variant()} ${props.class ?? ""}`}
      aria-busy="true"
    >
      <div class="loading-state-indicator">
        <Spinner size={variant() === "inline" ? "sm" : "md"} label={props.label} />
        <span>{props.label}</span>
      </div>
      <Show when={skeletonCount() > 0}>
        <div class="loading-state-skeletons" aria-hidden="true">
          <For each={Array.from({ length: skeletonCount() })}>
            {(_, index) => <Skeleton index={index()} class="loading-state-skeleton" />}
          </For>
        </div>
      </Show>
    </div>
  );
}
