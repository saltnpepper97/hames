import { For, Show, createMemo, createSignal, onCleanup } from "solid-js";
import type { DiffPresentation, DiffRow, TerminalPresentation } from "../toolPresentation";
import { formatToolDuration } from "../toolPresentation";

const CHAT_BODY_MAX_LINES = 10;

function splitRows<T>(rows: readonly T[], expanded: boolean) {
  if (expanded || rows.length <= CHAT_BODY_MAX_LINES) {
    return { head: rows, tail: [] as readonly T[], hidden: 0 };
  }
  const headCount = 6;
  const tailCount = 3;
  return {
    head: rows.slice(0, headCount),
    tail: rows.slice(-tailCount),
    hidden: rows.length - headCount - tailCount,
  };
}

function useCopyText(value: () => string) {
  const [copied, setCopied] = createSignal(false);
  let resetTimer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => {
    if (resetTimer) clearTimeout(resetTimer);
  });
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value());
      setCopied(true);
      if (resetTimer) clearTimeout(resetTimer);
      resetTimer = setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  };
  return { copied, copy };
}

function FoldControl(props: {
  hidden: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <Show when={props.hidden > 0 || props.expanded}>
      <button class="tool-card-fold" type="button" onClick={props.onToggle}>
        {props.expanded ? "Collapse output" : `Show ${props.hidden} more lines`}
      </button>
    </Show>
  );
}

function DiffLine(props: { row: DiffRow }) {
  return (
    <div class="diff-card-row" data-kind={props.row.kind}>
      <Show
        when={!(["file", "hunk", "meta"] as const).includes(props.row.kind as "file" | "hunk" | "meta")}
        fallback={<span class="diff-card-wide-label">{props.row.text}</span>}
      >
        <span class="diff-line-number">{props.row.oldLine ?? ""}</span>
        <span class="diff-line-number">{props.row.newLine ?? ""}</span>
        <span class="diff-line-sign" aria-hidden="true">
          {props.row.kind === "add" ? "+" : props.row.kind === "delete" ? "−" : " "}
        </span>
        <span class="diff-line-text">{props.row.text || " "}</span>
      </Show>
    </div>
  );
}

export function DiffCard(props: {
  diff: DiffPresentation;
  content: string;
  truncated?: boolean;
}) {
  const [expanded, setExpanded] = createSignal(false);
  const rows = createMemo(() => splitRows(props.diff.rows, expanded()));
  const clipboard = useCopyText(() => props.content || props.diff.rows.map((row) => {
    if (row.kind === "add") return `+${row.text}`;
    if (row.kind === "delete") return `-${row.text}`;
    return row.text;
  }).join("\n"));

  return (
    <section class="diff-card" aria-label={`Changes to ${props.diff.path || "file"}`}>
      <button class="tool-card-copy" type="button" onClick={() => void clipboard.copy()}>
        {clipboard.copied() ? "Copied" : "Copy diff"}
      </button>
      <div class="diff-card-body">
        <For each={rows().head}>{(row) => <DiffLine row={row} />}</For>
        <Show when={rows().hidden > 0}>
          <div class="diff-card-gap" aria-hidden="true">···</div>
        </Show>
        <For each={rows().tail}>{(row) => <DiffLine row={row} />}</For>
      </div>
      <div class="tool-card-footer">
        <span class="diff-added">+{props.diff.added}</span>
        <span class="diff-removed">−{props.diff.removed}</span>
        <span>· {props.diff.files} {props.diff.files === 1 ? "file" : "files"}</span>
        <Show when={props.truncated}><span>· retained output</span></Show>
        <FoldControl
          hidden={rows().hidden}
          expanded={expanded()}
          onToggle={() => setExpanded((value) => !value)}
        />
      </div>
    </section>
  );
}

interface TerminalRow {
  stream: "stdout" | "stderr";
  text: string;
  first: boolean;
}

function terminalRows(terminal: TerminalPresentation): TerminalRow[] {
  const rows: TerminalRow[] = [];
  for (const stream of ["stdout", "stderr"] as const) {
    const output = terminal[stream];
    if (!output) continue;
    output.split("\n").forEach((text, index) => rows.push({ stream, text, first: index === 0 }));
  }
  return rows;
}

export function TerminalCard(props: { terminal: TerminalPresentation }) {
  const [expanded, setExpanded] = createSignal(false);
  const outputRows = createMemo(() => terminalRows(props.terminal));
  const rows = createMemo(() => splitRows(outputRows(), expanded()));
  const copyText = () => [
    `$ ${props.terminal.command}`,
    props.terminal.stdout,
    props.terminal.stderr,
  ].filter(Boolean).join("\n");
  const clipboard = useCopyText(copyText);
  const commandLines = () => props.terminal.command.split("\n");
  const status = () => {
    if (props.terminal.running) return "Running";
    if (props.terminal.exitCode !== undefined) {
      return props.terminal.exitCode === 0 ? "Done" : `Exit ${props.terminal.exitCode}`;
    }
    if (props.terminal.state === "error") return "Failed";
    if (props.terminal.state === "warning") return "Stopped";
    return "Finished";
  };

  return (
    <section
      class="terminal-card"
      data-state={props.terminal.state}
      aria-label="Shell command"
    >
      <header class="terminal-card-command">
        <span class="terminal-run-dot" aria-hidden="true" />
        <div class="terminal-command-copy">
          <For each={commandLines()}>{(line, index) => (
            <div class="terminal-command-line">
              <span class="terminal-prompt">{index() === 0 ? "$" : ">"}</span>
              <code>{line || " "}</code>
            </div>
          )}</For>
          <Show when={props.terminal.cwd}>
            <span class="terminal-cwd" title={props.terminal.cwd}>{props.terminal.cwd}</span>
          </Show>
        </div>
        <span class="terminal-status">{status()}</span>
        <button class="tool-card-copy" type="button" onClick={() => void clipboard.copy()}>
          {clipboard.copied() ? "Copied" : "Copy"}
        </button>
      </header>
      <Show
        when={outputRows().length > 0}
        fallback={(
          <div class="terminal-empty">
            {props.terminal.running ? "Waiting for output…" : "Command completed without output."}
          </div>
        )}
      >
        <div class="terminal-output">
          <For each={rows().head}>{(row) => (
            <div class="terminal-output-line" data-stream={row.stream}>
              <Show when={row.first && outputRows().some((candidate) => candidate.stream !== row.stream)}>
                <span class="terminal-stream-label">{row.stream}</span>
              </Show>
              <code>{row.text || " "}</code>
            </div>
          )}</For>
          <Show when={rows().hidden > 0}>
            <div class="terminal-output-gap">···</div>
          </Show>
          <For each={rows().tail}>{(row) => (
            <div class="terminal-output-line" data-stream={row.stream}>
              <Show when={row.first && outputRows().some((candidate) => candidate.stream !== row.stream)}>
                <span class="terminal-stream-label">{row.stream}</span>
              </Show>
              <code>{row.text || " "}</code>
            </div>
          )}</For>
        </div>
      </Show>
      <Show when={formatToolDuration(props.terminal.durationSeconds) || props.terminal.truncated || rows().hidden > 0 || expanded()}>
        <footer class="tool-card-footer terminal-card-footer">
          <Show when={formatToolDuration(props.terminal.durationSeconds)}>
            <span>{formatToolDuration(props.terminal.durationSeconds)}</span>
          </Show>
          <Show when={props.terminal.truncated}><span>Retained output</span></Show>
          <FoldControl
            hidden={rows().hidden}
            expanded={expanded()}
            onToggle={() => setExpanded((value) => !value)}
          />
        </footer>
      </Show>
    </section>
  );
}
