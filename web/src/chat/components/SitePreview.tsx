import { Show, createEffect, createMemo, createSignal } from "solid-js";
import {
  IconArrowRight,
  IconExternalLink,
  IconMaximize,
  IconMinimize,
  IconRefresh,
  IconX,
} from "@tabler/icons-solidjs";
import { Button } from "../../components/Button";

const DEFAULT_PREVIEW_URL = "http://127.0.0.1:5173";

interface PreviewState {
  draftUrl: string;
  activeUrl: string;
  revision: number;
  requestId: string;
}

const previewStateBySession = new Map<string, PreviewState>();

function initialState(): PreviewState {
  return { draftUrl: DEFAULT_PREVIEW_URL, activeUrl: "", revision: 0, requestId: "" };
}

export function validateSitePreviewUrl(value: string): { url?: string; error?: string } {
  const input = value.trim();
  if (!input) return { error: "Enter a local site address, such as http://127.0.0.1:5173." };

  let url: URL;
  try {
    url = new URL(input);
  } catch {
    return { error: "Enter a full address beginning with http:// or https://." };
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { error: "Only local HTTP or HTTPS addresses can open in the preview." };
  }
  const authority = input.match(/^https?:\/\/([^/?#]*)/i)?.[1] ?? "";
  if (url.username || url.password || authority.includes("@")) {
    return { error: "Addresses with embedded usernames or passwords are not allowed." };
  }

  const host = url.hostname.toLowerCase();
  if (host !== "localhost" && host !== "127.0.0.1" && host !== "[::1]") {
    return { error: "Use localhost, 127.0.0.1, or [::1] for a local preview." };
  }
  if (typeof window !== "undefined" && url.origin === window.location.origin) {
    return { error: "The Hames address cannot preview itself. Enter your app's local port instead." };
  }

  return { url: url.href };
}

interface SitePreviewProps {
  sessionId: string;
  request: { id: string; url: string };
  open: boolean;
  expanded: boolean;
  onExpanded: (expanded: boolean) => void;
  onClose: () => void;
}

export function SitePreview(props: SitePreviewProps) {
  const initial = previewStateBySession.get(props.sessionId) ?? initialState();
  const [draftUrl, setDraftUrl] = createSignal(initial.draftUrl);
  const [activeUrl, setActiveUrl] = createSignal(initial.activeUrl);
  const [revision, setRevision] = createSignal(initial.revision);
  const [error, setError] = createSignal("");
  let currentSessionId = props.sessionId;
  let appliedRequestId = initial.requestId;

  const saveState = (sessionId = currentSessionId) => {
    previewStateBySession.set(sessionId, {
      draftUrl: draftUrl(),
      activeUrl: activeUrl(),
      revision: revision(),
      requestId: appliedRequestId,
    });
  };

  createEffect(() => {
    const nextSessionId = props.sessionId;
    if (nextSessionId === currentSessionId) return;
    saveState(currentSessionId);
    currentSessionId = nextSessionId;
    const next = previewStateBySession.get(nextSessionId) ?? initialState();
    setDraftUrl(next.draftUrl);
    setActiveUrl(next.activeUrl);
    setRevision(next.revision);
    setError("");
    appliedRequestId = next.requestId;
  });

  const applyUrl = (value: string) => {
    const result = validateSitePreviewUrl(value);
    if (!result.url) {
      setError(result.error ?? "Enter a valid local site address.");
      saveState();
      return false;
    }
    setError("");
    setDraftUrl(result.url);
    setActiveUrl(result.url);
    setRevision(current => current + 1);
    saveState();
    return true;
  };

  createEffect(() => {
    const request = props.request;
    if (request.id === appliedRequestId) return;
    appliedRequestId = request.id;
    applyUrl(request.url);
  });

  const frameTarget = createMemo(() => {
    const url = activeUrl();
    return props.open && url ? { url, revision: revision() } : undefined;
  });

  return (
    <aside
      class="site-preview"
      aria-label="Local site preview"
      aria-hidden={!props.open || undefined}
    >
      <div class="site-preview-toolbar">
        <div class="site-preview-heading">
          <strong>Site preview</strong>
          <div class="site-preview-actions">
            <Button
              variant="icon"
              class="site-preview-action"
              aria-label="Reload preview"
              title="Reload preview"
              disabled={!activeUrl()}
              onClick={() => setRevision(current => current + 1)}
            >
              <IconRefresh size={17} strokeWidth={1.7} aria-hidden="true" />
            </Button>
            <Button
              variant="icon"
              class="site-preview-action"
              aria-label="Open preview in a new tab"
              title="Open in new tab"
              disabled={!activeUrl()}
              onClick={() => {
                const url = activeUrl();
                if (url) window.open(url, "_blank", "noopener,noreferrer");
              }}
            >
              <IconExternalLink size={17} strokeWidth={1.7} aria-hidden="true" />
            </Button>
            <Button
              variant="icon"
              class="site-preview-action"
              aria-label={props.expanded ? "Restore preview sidebar" : "Expand preview"}
              title={props.expanded ? "Restore sidebar" : "Expand preview"}
              aria-pressed={props.expanded}
              onClick={() => props.onExpanded(!props.expanded)}
            >
              <Show when={props.expanded} fallback={<IconMaximize size={17} strokeWidth={1.7} aria-hidden="true" />}>
                <IconMinimize size={17} strokeWidth={1.7} aria-hidden="true" />
              </Show>
            </Button>
            <Button
              variant="icon"
              class="site-preview-action"
              aria-label="Close site preview"
              title="Close preview"
              onClick={props.onClose}
            >
              <IconX size={17} strokeWidth={1.7} aria-hidden="true" />
            </Button>
          </div>
        </div>
        <form
          class="site-preview-address"
          onSubmit={(event) => {
            event.preventDefault();
            applyUrl(draftUrl());
          }}
        >
          <label class="visually-hidden" for={`site-preview-address-${props.sessionId}`}>
            Local site address
          </label>
          <input
            id={`site-preview-address-${props.sessionId}`}
            class="site-preview-input text-input"
            type="text"
            inputMode="url"
            spellcheck={false}
            autocomplete="url"
            value={draftUrl()}
            aria-invalid={Boolean(error())}
            aria-describedby={error()
              ? `site-preview-error-${props.sessionId}`
              : `site-preview-help-${props.sessionId}`}
            onInput={(event) => {
              setDraftUrl(event.currentTarget.value);
              setError("");
              saveState();
            }}
          />
          <Button type="submit" variant="icon" class="site-preview-go" aria-label="Go to local site" title="Go">
            <IconArrowRight size={17} strokeWidth={1.7} aria-hidden="true" />
          </Button>
        </form>
        <Show when={error()}>
          <p class="site-preview-error" id={`site-preview-error-${props.sessionId}`} role="alert">{error()}</p>
        </Show>
        <p class="site-preview-help" id={`site-preview-help-${props.sessionId}`}>
          This address is reached from this device. On a phone, localhost refers to the phone. Some sites block embedding; if the page is blank, try Open in new tab.
        </p>
      </div>
      <div class="site-preview-frame">
        <Show when={frameTarget()} keyed fallback={(
          <div class="site-preview-empty">Enter the local address for the site you want to view.</div>
        )}>
          {(target) => (
            <iframe
              title="Local site"
              src={target.url}
              sandbox="allow-forms allow-scripts allow-same-origin"
              referrerPolicy="no-referrer"
            />
          )}
        </Show>
      </div>
    </aside>
  );
}
