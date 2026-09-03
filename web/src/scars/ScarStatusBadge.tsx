import type { ScarSeverity, ScarStatus } from "../api/types";

function label(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

interface ScarStatusBadgeProps {
  status?: ScarStatus;
  severity?: ScarSeverity;
}

export function ScarStatusBadge(props: ScarStatusBadgeProps) {
  const value = () => props.status ?? props.severity ?? "open";
  const kind = () => props.status ? "status" : "severity";
  return (
    <span class="scar-badge" data-kind={kind()} data-value={value()}>
      {label(value())}
    </span>
  );
}
