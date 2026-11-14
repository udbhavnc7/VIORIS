import { useMemo, useState } from "react";

import { api, formatError, type Approval, type RiskTier } from "../lib/api";
import { displayValue, relativeTime, RISK_COPY, shortId, toolInfo } from "../lib/format";
import { useNow } from "../hooks/useNow";
import { useData } from "../state/store";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { RiskBadge } from "./Pills";
import { useToast } from "./Toast";

const FIELD_LABEL: Record<string, string> = {
  recipient: "Recipient",
  recipient_identity: "Recipient identity",
  content: "Message",
  channel: "Channel",
  payee: "Payee",
  currency: "Currency",
  amount: "Amount",
  account: "Account",
  device_id: "Device",
  device_name: "Device",
  category: "Category",
  action: "Action",
  value: "Value",
  url: "URL",
  element: "Element",
  source: "Source",
  destination: "Destination",
  path: "Path",
  new_name: "New name",
  command: "Command",
  args: "Arguments",
  phone: "Phone",
  script: "Script",
  venue: "Venue",
  at: "When",
  party_size: "Party size",
  guest_name: "Guest",
  slot_id: "Slot",
  timeout_minutes: "Session length",
};

const CRITICAL_COOLDOWN_MS = 5000;

function tierOf(approval: Approval): RiskTier {
  return approval.risk_level ?? "execute";
}

function summary(approval: Approval): string {
  const info = toolInfo(approval.tool);
  const diff = approval.diff_card ?? {};
  if (approval.tool === "system.send_message" || approval.tool === "whatsapp.send_message") {
    return `Send “${String(diff.content ?? "…")}” to ${String(diff.recipient ?? "someone")}.`;
  }
  if (approval.tool === "computer.start_remote_session") {
    return `Open a ${String(diff.timeout_minutes ?? "5")}-minute control session on this laptop.`;
  }
  if (approval.tool === "smart_home.control") {
    return `Set ${String(diff.device_name ?? diff.device_id ?? "a device")} to ${String(diff.value ?? diff.action ?? "a new state")}.`;
  }
  if (approval.tool === "reservations.create") {
    return `Book ${String(diff.venue ?? "a table")} for ${String(diff.party_size ?? 2)} at ${String(diff.at ?? "the chosen time")}.`;
  }
  return info.blurb;
}

export function ApprovalCard({
  approval,
  compact = false,
  onDecided,
}: {
  approval: Approval;
  compact?: boolean;
  onDecided?: () => void;
}) {
  const tier = tierOf(approval);
  const info = toolInfo(approval.tool);
  const { system, refreshCore, refreshSystem } = useData();
  const { push } = useToast();
  const now = useNow(1000);
  const [busy, setBusy] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);

  const created = useMemo(() => {
    const iso = approval.created_at;
    if (!iso) return 0;
    return new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
  }, [approval.created_at]);
  const cooldownLeft = Math.max(0, Math.ceil((created + CRITICAL_COOLDOWN_MS - now) / 1000));
  const fields = Object.entries(approval.diff_card ?? {});

  const finish = async (label: string, tone: "ok" | "warn") => {
    push({ title: label, message: "The audit chain recorded it.", tone });
    await Promise.all([refreshCore(), refreshSystem()]);
    onDecided?.();
  };

  const approve = async (withPin?: string) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.approve(approval.approval_id, withPin);
      const task = result.task as { status?: string } | null | undefined;
      setPinOpen(false);
      setPin("");
      if (task?.status === "failed") {
        await finish("Approved — the step ran and failed", "warn");
      } else {
        await finish("Approved", "ok");
      }
    } catch (err) {
      setError(formatError(err));
      if (!withPin) push({ title: "Could not approve", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.reject(approval.approval_id);
      await finish("Rejected", "warn");
    } catch (err) {
      setError(formatError(err));
      push({ title: "Could not reject", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  const requestApprove = () => {
    if (tier === "critical") {
      setError(null);
      setPinOpen(true);
      return;
    }
    void approve();
  };

  return (
    <article className={`approval-card risk-edge-${tier} ${compact ? "is-compact" : ""}`}>
      <div className="approval-top">
        <RiskBadge tier={tier} />
        <span className="service-chip">
          <Icon name={tier === "critical" ? "lock" : "shield"} size={12} />
          {info.service}
        </span>
        <span className="approval-time">{relativeTime(approval.created_at)}</span>
      </div>

      <h3>{info.label}</h3>
      <p className="approval-summary">{summary(approval)}</p>

      {fields.length > 0 ? (
        <dl className="diff-table">
          {fields.map(([key, value]) => (
            <div className="diff-row" key={key}>
              <dt>{FIELD_LABEL[key] ?? key.replace(/_/g, " ")}</dt>
              <dd className={key === "content" || key === "script" ? "is-copy" : ""}>
                {displayValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="approval-summary muted">No diff fields were registered for this tool.</p>
      )}

      <p className="approval-consequence">
        <Icon name="alert" size={14} />
        {RISK_COPY[tier]}
      </p>

      {error && !pinOpen ? <p className="inline-error">{error}</p> : null}

      <div className="approval-actions">
        <button type="button" className="btn btn-ghost" onClick={reject} disabled={busy}>
          Reject
        </button>
        <button type="button" className="btn btn-primary" onClick={requestApprove} disabled={busy}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="check" size={16} />}
          {tier === "critical" ? "Approve with PIN" : "Approve"}
        </button>
      </div>

      <div className="approval-meta">
        <span>
          task <code>{shortId(approval.task_id)}</code>
        </span>
        <span>
          step <code>{shortId(approval.step_id)}</code>
        </span>
      </div>

      <Modal
        open={pinOpen}
        onClose={() => {
          setPinOpen(false);
          setError(null);
        }}
        title="Confirm a Critical action"
        eyebrow="Second factor"
        size="sm"
        footer={
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setPinOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => void approve(pin)}
              disabled={busy || cooldownLeft > 0 || !pin}
            >
              {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="lockOpen" size={16} />}
              Confirm
            </button>
          </>
        }
      >
        <div className="pin-body">
          <p>
            This action is irreversible enough that Vioris asks for a local second factor. The PIN
            never leaves this machine and is checked again before the step fires.
          </p>
          {system?.critical_actions === "locked" ? (
            <div className="callout callout-warn">
              <Icon name="lock" size={16} />
              <div>
                <strong>Critical actions are locked.</strong>
                <span>{system.critical_note}</span>
              </div>
            </div>
          ) : null}
          <label className="field">
            <span>Local PIN</span>
            <input
              type="password"
              value={pin}
              autoComplete="off"
              inputMode="numeric"
              placeholder="Enter VIORIS_CRITICAL_PIN"
              onChange={(event) => setPin(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && pin && cooldownLeft === 0) void approve(pin);
              }}
            />
          </label>
          {cooldownLeft > 0 ? (
            <div className="cooldown">
              <span className="cooldown-ring" />
              Hold for {cooldownLeft}s — the cooldown is part of the gate.
            </div>
          ) : (
            <p className="fine-print">Cooldown complete. You can confirm now.</p>
          )}
          {error ? <p className="inline-error">{error}</p> : null}
        </div>
      </Modal>
    </article>
  );
}
