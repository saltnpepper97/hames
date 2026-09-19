import {
  For,
  createEffect,
  createMemo,
  createSignal,
  createUniqueId,
  onCleanup,
  onMount,
} from "solid-js";
import type { JSX } from "solid-js";
import { Portal } from "solid-js/web";

export interface SelectOption {
  value: string;
  label: string;
  leading?: JSX.Element;
  description?: string;
  disabled?: boolean;
}

export interface SelectProps {
  id?: string;
  value?: string;
  options: readonly SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  name?: string;
  form?: string;
  class?: string;
  ariaLabel?: string;
  ariaDescribedBy?: string;
  ariaInvalid?: boolean;
  leading?: JSX.Element;
  onValueChange: (value: string) => void;
}

type PlacementSide = "top" | "bottom";
type PlacementAlign = "start" | "end";

export function Select(props: SelectProps) {
  const generatedId = `select-${createUniqueId()}`;
  const id = () => props.id ?? generatedId;
  const listboxId = () => `${id()}-listbox`;
  const [open, setOpen] = createSignal(false);
  const [activeIndex, setActiveIndex] = createSignal(-1);
  const [placementSide, setPlacementSide] = createSignal<PlacementSide>("bottom");
  const [placementAlign, setPlacementAlign] = createSignal<PlacementAlign>("start");
  const [placementStyle, setPlacementStyle] = createSignal("");
  const [placementReady, setPlacementReady] = createSignal(false);
  let field!: HTMLDivElement;
  let frame!: HTMLDivElement;
  let trigger!: HTMLButtonElement;
  let content: HTMLDivElement | undefined;
  let typeahead = "";
  let typeaheadTimer: ReturnType<typeof setTimeout> | undefined;
  let placementFrame: number | undefined;

  const selectedIndex = createMemo(() =>
    props.options.findIndex((option) => option.value === props.value)
  );
  const selectedOption = createMemo(() => props.options[selectedIndex()]);
  const activeOptionId = createMemo(() =>
    open() && activeIndex() >= 0 ? `${id()}-option-${activeIndex()}` : undefined
  );

  const firstEnabledIndex = () => props.options.findIndex((option) => !option.disabled);
  const lastEnabledIndex = () => {
    for (let index = props.options.length - 1; index >= 0; index -= 1) {
      if (!props.options[index]?.disabled) return index;
    }
    return -1;
  };
  const nextEnabledIndex = (from: number, direction: 1 | -1) => {
    if (!props.options.length) return -1;
    for (let offset = 1; offset <= props.options.length; offset += 1) {
      const index = (from + direction * offset + props.options.length) % props.options.length;
      if (!props.options[index]?.disabled) return index;
    }
    return -1;
  };

  const positionContent = () => {
    if (!open() || !content) return;
    content.style.removeProperty("max-height");
    const triggerRect = trigger.getBoundingClientRect();
    const viewportPadding = 8;
    const contentGap = 5;
    const ownerDocument = content.ownerDocument;
    const ownerWindow = ownerDocument.defaultView ?? window;
    const viewportWidth = Math.min(
      ownerWindow.innerWidth,
      ownerDocument.documentElement.clientWidth || ownerWindow.innerWidth,
      ownerWindow.visualViewport?.width ?? Number.POSITIVE_INFINITY,
    );
    const viewportHeight = Math.min(
      ownerWindow.innerHeight,
      ownerDocument.documentElement.clientHeight || ownerWindow.innerHeight,
      ownerWindow.visualViewport?.height ?? Number.POSITIVE_INFINITY,
    );
    const clippingLeft = viewportPadding;
    const clippingRight = viewportWidth - viewportPadding;
    const clippingTop = viewportPadding;
    const clippingBottom = viewportHeight - viewportPadding;

    const spaceBelow = Math.max(0, clippingBottom - triggerRect.bottom - contentGap);
    const spaceAbove = Math.max(0, triggerRect.top - clippingTop - contentGap);
    const naturalHeight = content.offsetHeight;
    const naturalWidth = content.offsetWidth;
    const side: PlacementSide = naturalHeight <= spaceBelow || spaceBelow >= spaceAbove
      ? "bottom"
      : "top";
    const availableHeight = side === "bottom" ? spaceBelow : spaceAbove;
    const placedHeight = Math.min(naturalHeight, availableHeight);
    const startLeft = triggerRect.left;
    const endLeft = triggerRect.right - naturalWidth;
    let align: PlacementAlign;
    let viewportLeft: number;

    if (startLeft >= clippingLeft && startLeft + naturalWidth <= clippingRight) {
      align = "start";
      viewportLeft = startLeft;
    } else if (endLeft >= clippingLeft && endLeft + naturalWidth <= clippingRight) {
      align = "end";
      viewportLeft = endLeft;
    } else {
      align = triggerRect.left > viewportWidth / 2 ? "end" : "start";
      viewportLeft = Math.min(
        Math.max(startLeft, clippingLeft),
        Math.max(clippingLeft, clippingRight - naturalWidth),
      );
    }

    const viewportTop = side === "bottom"
      ? triggerRect.bottom + contentGap
      : triggerRect.top - contentGap - placedHeight;
    setPlacementSide(side);
    setPlacementAlign(align);
    setPlacementStyle(
      `left:${viewportLeft}px;top:${viewportTop}px;max-height:${placedHeight}px;--select-available-height:${placedHeight}px;--select-trigger-width:${triggerRect.width}px`,
    );
    setPlacementReady(true);
  };

  const schedulePlacement = () => {
    if (!open()) return;
    if (placementFrame !== undefined) cancelAnimationFrame(placementFrame);
    placementFrame = requestAnimationFrame(() => {
      placementFrame = undefined;
      positionContent();
    });
  };

  const setActive = (index: number) => {
    setActiveIndex(index);
    queueMicrotask(() => {
      schedulePlacement();
      const active = content?.querySelector<HTMLElement>("[data-active]");
      active?.scrollIntoView?.({ block: "nearest" });
    });
  };

  const close = () => {
    setOpen(false);
    setPlacementReady(false);
    setActiveIndex(-1);
    typeahead = "";
  };

  const openSelect = (direction: "first" | "last" = "first") => {
    if (props.disabled || !props.options.length) return;
    setPlacementReady(false);
    setOpen(true);
    const selected = selectedIndex();
    setActive(
      selected >= 0 && !props.options[selected]?.disabled
        ? selected
        : direction === "last" ? lastEnabledIndex() : firstEnabledIndex(),
    );
  };

  const choose = (index: number) => {
    const option = props.options[index];
    if (!option || option.disabled) return;
    if (option.value !== props.value) props.onValueChange(option.value);
    close();
    queueMicrotask(() => trigger.focus());
  };

  const runTypeahead = (key: string) => {
    typeahead += key.toLocaleLowerCase();
    if (typeaheadTimer) clearTimeout(typeaheadTimer);
    typeaheadTimer = setTimeout(() => { typeahead = ""; }, 500);
    const start = activeIndex() < 0 ? 0 : activeIndex() + 1;
    for (let offset = 0; offset < props.options.length; offset += 1) {
      const index = (start + offset) % props.options.length;
      const option = props.options[index];
      if (option && !option.disabled && option.label.toLocaleLowerCase().startsWith(typeahead)) {
        setActive(index);
        return;
      }
    }
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (props.disabled) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open()) openSelect();
        else setActive(nextEnabledIndex(activeIndex(), 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!open()) openSelect("last");
        else setActive(nextEnabledIndex(activeIndex(), -1));
        break;
      case "Home":
        if (open()) {
          event.preventDefault();
          setActive(firstEnabledIndex());
        }
        break;
      case "End":
        if (open()) {
          event.preventDefault();
          setActive(lastEnabledIndex());
        }
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        if (open() && activeIndex() >= 0) choose(activeIndex());
        else openSelect();
        break;
      case "Escape":
        if (open()) {
          event.preventDefault();
          close();
        }
        break;
      case "Tab":
        close();
        break;
      default:
        if (open() && event.key.length === 1 && /\S/.test(event.key)) runTypeahead(event.key);
    }
  };

  onMount(() => {
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (open() && !field.contains(target) && !content?.contains(target)) close();
    };
    document.addEventListener("pointerdown", closeOutside);
    window.addEventListener("resize", schedulePlacement);
    window.addEventListener("scroll", schedulePlacement, true);
    window.visualViewport?.addEventListener("resize", schedulePlacement);
    window.visualViewport?.addEventListener("scroll", schedulePlacement);
    onCleanup(() => {
      document.removeEventListener("pointerdown", closeOutside);
      window.removeEventListener("resize", schedulePlacement);
      window.removeEventListener("scroll", schedulePlacement, true);
      window.visualViewport?.removeEventListener("resize", schedulePlacement);
      window.visualViewport?.removeEventListener("scroll", schedulePlacement);
    });
  });

  createEffect(() => {
    if (props.disabled && open()) close();
  });

  createEffect(() => {
    if (open()) schedulePlacement();
  });

  onCleanup(() => {
    if (typeaheadTimer) clearTimeout(typeaheadTimer);
    if (placementFrame !== undefined) cancelAnimationFrame(placementFrame);
  });

  return (
    <div ref={field} class={`select-field-control ${props.class ?? ""}`} data-open={open() ? "" : undefined}>
      <div ref={frame} class="select-frame" data-open={open() ? "" : undefined}>
        <button
          ref={trigger}
          id={id()}
          type="button"
          class="select-trigger"
          role="combobox"
          disabled={props.disabled}
          aria-label={props.ariaLabel}
          aria-haspopup="listbox"
          aria-expanded={open()}
          aria-controls={listboxId()}
          aria-activedescendant={activeOptionId()}
          aria-describedby={props.ariaDescribedBy}
          aria-invalid={props.ariaInvalid || undefined}
          aria-required={props.required || undefined}
          data-component="select"
          data-open={open() ? "" : undefined}
          data-placeholder={selectedOption() ? undefined : ""}
          onClick={() => { if (open()) close(); else openSelect(); }}
          onKeyDown={handleKeyDown}
          onFocusOut={(event) => {
            const next = event.relatedTarget;
            if (next instanceof Node && !field.contains(next)) close();
          }}
        >
          {props.leading && <span class="select-leading">{props.leading}</span>}
          <span class="select-value" title={selectedOption()?.label}>
            {selectedOption()?.label ?? props.placeholder ?? "Select an option"}
          </span>
          <svg class="select-control-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="m4.5 6 3.5 3.5L11.5 6" />
          </svg>
        </button>

        {open() && (
          <Portal>
            <div
              ref={content}
              class="select-options"
              style={placementStyle()}
              data-component="select-options"
              data-side={placementSide()}
              data-align={placementAlign()}
              data-placement-ready={placementReady() ? "" : undefined}
            >
              <div id={listboxId()} class="select-options-scroll" role="listbox" aria-label={props.ariaLabel}>
                <For each={props.options}>
                  {(option, index) => (
                    <div
                      id={`${id()}-option-${index()}`}
                      class="select-option"
                      role="option"
                      tabIndex={-1}
                      aria-selected={selectedIndex() === index()}
                      aria-disabled={option.disabled || undefined}
                      data-active={activeIndex() === index() ? "" : undefined}
                      data-selected={selectedIndex() === index() ? "" : undefined}
                      data-disabled={option.disabled ? "" : undefined}
                      data-value={option.value}
                      onPointerDown={(event) => event.preventDefault()}
                      onPointerMove={() => { if (!option.disabled) setActiveIndex(index()); }}
                      onClick={() => choose(index())}
                    >
                      <span class="select-option-copy">
                        <span class="select-option-label">{option.leading}{option.label}</span>
                        {option.description && <small>{option.description}</small>}
                      </span>
                      <svg class="select-option-check" viewBox="0 0 16 16" aria-hidden="true">
                        <path d="m3.5 8 3 3 6-6" />
                      </svg>
                    </div>
                  )}
                </For>
              </div>
            </div>
          </Portal>
        )}
      </div>
      {props.name && (
        <input type="hidden" name={props.name} form={props.form} value={props.value ?? ""} disabled={props.disabled} />
      )}
    </div>
  );
}
