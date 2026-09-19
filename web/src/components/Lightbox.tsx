import { Show, createEffect, createSignal, createUniqueId, onCleanup, onMount } from "solid-js";
import { Portal } from "solid-js/web";
import { CloseButton } from "./CloseButton";
import { Spinner } from "./Spinner";

export interface LightboxItem {
  name: string;
  kind: "image" | "text";
  mediaType?: string;
  size?: number;
  source?: string;
  content?: string;
}

interface LightboxProps {
  item: LightboxItem;
  onClose: () => void;
}

function formatSize(size?: number): string {
  if (!size) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function Lightbox(props: LightboxProps) {
  const titleId = `lightbox-${createUniqueId()}`;
  const [content, setContent] = createSignal(props.item.content ?? "");
  const [loading, setLoading] = createSignal(props.item.kind === "text" && props.item.content === undefined);
  const [error, setError] = createSignal("");
  let closeButton: HTMLButtonElement | undefined;
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  createEffect(() => {
    const item = props.item;
    if (item.kind !== "text" || item.content !== undefined) {
      setContent(item.content ?? "");
      setLoading(false);
      setError("");
      return;
    }
    if (!item.source) {
      setLoading(false);
      setError("This file has no preview source.");
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void fetch(item.source, { credentials: "same-origin", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Preview failed (${response.status})`);
        return response.text();
      })
      .then(setContent)
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "Unable to render this file.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    onCleanup(() => controller.abort());
  });

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    props.onClose();
  };

  onMount(() => {
    document.addEventListener("keydown", onKeyDown);
    closeButton?.focus();
  });
  onCleanup(() => {
    document.removeEventListener("keydown", onKeyDown);
    previousFocus?.focus();
  });

  return (
    <Portal>
      <div class="lightbox-backdrop" role="presentation" onMouseDown={(event) => {
        if (event.target === event.currentTarget) props.onClose();
      }}>
        <Show when={props.item.kind === "image"} fallback={
          <section class="lightbox" role="dialog" aria-modal="true" aria-labelledby={titleId}>
            <header class="lightbox-header">
              <div>
                <h2 id={titleId} title={props.item.name}>{props.item.name}</h2>
                <p>
                  <Show when={props.item.mediaType}>{props.item.mediaType}</Show>
                  <Show when={props.item.mediaType && props.item.size}> · </Show>
                  <Show when={props.item.size}>{formatSize(props.item.size)}</Show>
                </p>
              </div>
              <CloseButton ref={closeButton} aria-label="Close preview" onClick={props.onClose} />
            </header>
            <div class="lightbox-content" data-kind="text">
              <Show when={!loading()} fallback={
                <div class="lightbox-state"><Spinner /><span>Rendering file…</span></div>
              }>
                <Show when={!error()} fallback={<div class="lightbox-state error" role="alert">{error()}</div>}>
                  <pre><code>{content()}</code></pre>
                </Show>
              </Show>
            </div>
            <Show when={props.item.source}>
              <footer class="lightbox-footer">
                <a href={props.item.source} download={props.item.name}>Download original</a>
              </footer>
            </Show>
          </section>
        }>
          <div class="lightbox-image" role="dialog" aria-modal="true" aria-label={props.item.name}>
            <Show when={!error()} fallback={<div class="lightbox-state error" role="alert">{error()}</div>}>
              <img
                src={props.item.source}
                alt={props.item.name}
                onError={() => setError("Unable to render this image.")}
              />
            </Show>
            <CloseButton
              ref={closeButton}
              class="lightbox-image-close"
              aria-label="Close preview"
              onClick={props.onClose}
            />
          </div>
        </Show>
      </div>
    </Portal>
  );
}
