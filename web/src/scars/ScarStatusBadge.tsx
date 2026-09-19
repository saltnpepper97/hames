import type { ScarSeverity, ScarStatus } from "../api/types";
import { Badge } from "../components/Badge";
import type { BadgeVariant } from "../components/Badge";

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
  const variant = (): BadgeVariant => {
    if (props.severity === "high" || props.status === "regressed") return "destructive";
    if (props.severity === "medium" || ["candidate", "open", "repair_proposed"].includes(props.status ?? "")) {
      return "warning";
    }
    if (props.status === "guarded") return "info";
    if (props.status === "healed") return "success";
    return "neutral";
  };
  return (
    <Badge class="scar-badge" variant={variant()} size="sm" data-kind={kind()} data-value={value()}>
      {label(value())}
    </Badge>
  );
}
