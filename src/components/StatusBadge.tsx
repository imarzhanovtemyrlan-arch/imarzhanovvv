import type { ExchangeHealth } from "../types/api";
import "./StatusBadge.css";

const LABELS: Record<ExchangeHealth["status"], string> = {
  connected: "Connected",
  connecting: "Connecting",
  disconnected: "Disconnected",
  error: "Error",
  unsupported: "Unsupported",
};

export function StatusBadge({ status }: { status: ExchangeHealth["status"] }) {
  return <span className={`status-badge status-badge--${status}`}>{LABELS[status]}</span>;
}
