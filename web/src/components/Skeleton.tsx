import { createMemo, splitProps } from "solid-js";
import type { JSX } from "solid-js";

export type SkeletonRadius = "none" | 0 | 1 | 2 | 3 | 4 | "rounded";
export type SkeletonSizeValue = number | string;

export interface SkeletonProps extends Omit<
  JSX.HTMLAttributes<HTMLDivElement>,
  "aria-hidden" | "children" | "role"
> {
  width?: SkeletonSizeValue;
  height?: SkeletonSizeValue;
  radius?: SkeletonRadius;
  index?: number;
}

function cssLength(value: SkeletonSizeValue): string {
  return typeof value === "number" ? `${value}px` : value;
}

export function Skeleton(props: SkeletonProps) {
  const [local, restProps] = splitProps(props, [
    "width",
    "height",
    "radius",
    "index",
    "class",
    "style",
  ]);
  const width = () => local.width ?? "100%";
  const height = () => local.height ?? "100%";
  const radius = () => local.radius ?? 3;
  const index = () => local.index ?? 0;
  const style = createMemo<JSX.CSSProperties | string>(() => {
    const variables = {
      "--_still-skeleton-width": cssLength(width()),
      "--_still-skeleton-height": cssLength(height()),
      "--_still-skeleton-index-delay": `${Math.max(0, 100 + index() * 65)}ms`,
    };
    if (typeof local.style === "string") {
      return `${local.style}; ${Object.entries(variables)
        .map(([name, value]) => `${name}: ${value}`)
        .join("; ")}`;
    }
    return { ...local.style, ...variables };
  });

  return (
    <div
      {...restProps}
      class={`still-skeleton ${local.class ?? ""}`}
      style={style()}
      aria-hidden="true"
      data-component="skeleton"
      data-radius={String(radius())}
      data-index={index()}
    />
  );
}
