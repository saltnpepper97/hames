export type ConnectionState = "connecting" | "connected" | "reconnecting" | "offline";

interface ConnectionStatusProps {
  state: ConnectionState;
}

const labels: Record<ConnectionState, string> = {
  connecting: "Connecting",
  connected: "Connected",
  reconnecting: "Reconnecting",
  offline: "Offline",
};

export function ConnectionStatus(props: ConnectionStatusProps) {
  return (
    <span class="connection-status" data-state={props.state} role="status">
      <span class="connection-dot" aria-hidden="true" />
      {labels[props.state]}
    </span>
  );
}
