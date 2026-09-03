export type ConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "offline"
  | "expired";

interface ConnectionStatusProps {
  state: ConnectionState;
  compact?: boolean;
}

const labels: Record<ConnectionState, string> = {
  connecting: "Connecting",
  connected: "Connected",
  reconnecting: "Reconnecting",
  offline: "Offline",
  expired: "Reopen Hames Web",
};

export function ConnectionStatus(props: ConnectionStatusProps) {
  return (
    <span
      class="connection-status"
      classList={{ compact: props.compact }}
      data-state={props.state}
      role="status"
      title={props.compact ? labels[props.state] : undefined}
    >
      <span class="connection-dot" aria-hidden="true" />
      <span class="connection-label">{labels[props.state]}</span>
    </span>
  );
}
