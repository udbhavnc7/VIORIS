import type { ConnectionMode, RiskTier, TaskStatus } from "../lib/api";
import { RISK_LABEL, RISK_COPY, STATUS_LABEL } from "../lib/format";

const STATUS_TONE: Record<TaskStatus, "ok" | "warn" | "bad" | "info" | "muted"> = {
  created: "muted",
  running: "info",
  paused: "muted",
  waiting_approval: "warn",
  completed: "ok",
  failed: "bad",
  cancelled: "muted",
  stopped: "bad",
};

export function RiskBadge({
  tier,
  withCopy = false,
}: {
  tier: RiskTier;
  withCopy?: boolean;
}) {
  return (
    <span className={`risk-badge risk-${tier}`} title={RISK_COPY[tier]}>
      <span className="risk-dot" aria-hidden="true" />
      {RISK_LABEL[tier]}
      {withCopy ? <em>{RISK_COPY[tier]}</em> : null}
    </span>
  );
}

export function StatusPill({ status }: { status: TaskStatus }) {
  return (
    <span className={`status-pill tone-${STATUS_TONE[status]}`}>
      <span className="status-dot" aria-hidden="true" />
      {STATUS_LABEL[status]}
    </span>
  );
}

const MODE_LABEL: Record<ConnectionMode, string> = {
  live: "Live",
  demo: "Sample data",
  local: "Local",
  off: "Not configured",
};

export function ModePill({ mode }: { mode: ConnectionMode }) {
  const tone =
    mode === "live" ? "ok" : mode === "demo" ? "info" : mode === "local" ? "violet" : "muted";
  return <span className={`mode-pill mode-${tone}`}>{MODE_LABEL[mode]}</span>;
}

export function LiveDot({ live }: { live: boolean }) {
  return (
    <span className={`live-dot ${live ? "is-live" : "is-off"}`} aria-hidden="true">
      <span />
    </span>
  );
}
