import { createMemo, createSignal } from "solid-js";
import { hexToHsv, hsvToHex } from "./color";

interface ColorWheelProps {
  value: string;
  onInput: (value: string) => void;
}

export function ColorWheel(props: ColorWheelProps) {
  const hsv = createMemo(() => hexToHsv(props.value));
  const [dragging, setDragging] = createSignal<"hue" | "field" | null>(null);

  const setHueFromPointer = (event: PointerEvent & { currentTarget: HTMLDivElement }) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left - bounds.width / 2;
    const y = event.clientY - bounds.top - bounds.height / 2;
    const hue = (Math.atan2(y, x) * 180 / Math.PI + 450) % 360;
    props.onInput(hsvToHex({ ...hsv(), h: hue }));
  };

  const setFieldFromPointer = (event: PointerEvent & { currentTarget: HTMLDivElement }) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    props.onInput(hsvToHex({
      h: hsv().h,
      s: Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
      v: 1 - Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height)),
    }));
  };

  const beginDrag = (kind: "hue" | "field", event: PointerEvent & { currentTarget: HTMLDivElement }) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(kind);
    if (kind === "hue") setHueFromPointer(event);
    else setFieldFromPointer(event);
  };

  const moveDrag = (event: PointerEvent & { currentTarget: HTMLDivElement }) => {
    if (dragging() === "hue") setHueFromPointer(event);
    if (dragging() === "field") setFieldFromPointer(event);
  };

  const nudge = (kind: "hue" | "saturation" | "value", event: KeyboardEvent) => {
    const direction = event.key === "ArrowRight" || event.key === "ArrowUp" ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowDown" ? -1 : 0;
    if (!direction) return;
    event.preventDefault();
    const current = hsv();
    props.onInput(hsvToHex({
      h: kind === "hue" ? current.h + direction * 3 : current.h,
      s: kind === "saturation" ? current.s + direction * 0.02 : current.s,
      v: kind === "value" ? current.v + direction * 0.02 : current.v,
    }));
  };

  return (
    <div class="color-picker">
      <div
        class="hue-wheel"
        role="slider"
        tabIndex={0}
        aria-label="Hue"
        aria-valuemin="0"
        aria-valuemax="359"
        aria-valuenow={Math.round(hsv().h)}
        onPointerDown={(event) => beginDrag("hue", event)}
        onPointerMove={moveDrag}
        onPointerUp={() => setDragging(null)}
        onPointerCancel={() => setDragging(null)}
        onKeyDown={(event) => nudge("hue", event)}
      >
        <span
          class="hue-cursor"
          style={{ transform: `rotate(${hsv().h}deg) translateY(-76px)` }}
        />
      </div>
      <div
        class="color-field"
        style={{ "--selected-hue": `hsl(${hsv().h} 100% 50%)` }}
        role="slider"
        tabIndex={0}
        aria-label="Color saturation"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={Math.round(hsv().s * 100)}
        onPointerDown={(event) => beginDrag("field", event)}
        onPointerMove={moveDrag}
        onPointerUp={() => setDragging(null)}
        onPointerCancel={() => setDragging(null)}
        onKeyDown={(event) => nudge(event.shiftKey ? "value" : "saturation", event)}
      >
        <span
          class="color-field-cursor"
          style={{ left: `${hsv().s * 100}%`, top: `${(1 - hsv().v) * 100}%` }}
        />
      </div>
      <p class="color-wheel-help">Arrows change saturation. Hold Shift for brightness.</p>
    </div>
  );
}
