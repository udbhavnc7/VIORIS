import { useState } from "react";

import { api, formatError, type Stage } from "../lib/api";
import { displayValue, toolInfo } from "../lib/format";
import { useNow } from "../hooks/useNow";
import { useData } from "../state/store";
import { Icon } from "./Icon";
import { useToast } from "./Toast";

const FIELD_LABEL: Record<string, string> = {
  recipient: "Recipient",
  content: "Message",
  payee: "Payee",
  amount: "Amount",
  currency: "Currency",
  path: "Path",
};

function secondsLeft(stage: Stage, now: number): number {
  const created = new Date(stage.created_at).getTime();
  if (Number.isNaN(created)) return 0;
  const deadline =
    stage.status === "executed"
      ? created + (stage.execute_after_seconds + stage.undo_window_seconds) * 1000
      : created + stage.execute_after_seconds * 1000;
  return Math.max(0, Math.ceil((deadline - now) / 1000));
}

export function StageCard({ stage }: { stage: Stage }) {
  const { refreshCore } = useData();
  const { push } = useToast();
  const now = useNow(1000);
  const [busy, setBusy] = useState(false);
  const info = toolInfo(stage.tool);
  const left = secondsLeft(stage, now);
  const fields = Object.entries(stage.diff_card ?? {}).slice(0, 6);

  // An executed stage whose undo window closed is no longer actionable.
  if (stage.status === "executed" && (!stage.undoable || left === 0)) return null;

  const cancel = async () => {
    setBusy(true);
    try {
      await api.cancelStage(stage.stage_id);
      push({ title: "Cancelled — nothing ran", message: "The step was stopped before it fired.", tone: "warn" });
      await refreshCore();
    } catch (err) {
      push({ title: "Could not cancel", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  const undo = async () => {
    setBusy(true);
    try {
      await api.undoStage(stage.stage_id);
      push({ title: "Undone", message: "The reversal ran and the audit chain recorded it.", tone: "ok" });
      await refreshCore();
    } catch (err) {
      push({ title: "Could not undo", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="approval-card risk-edge-critical">
      <div className="approval-top">
        <span className="service-chip">
          <Icon name="clock" size={12} />
          {stage.status === "staged" ? "Executing soon" : "Just executed"}
        </span>
        <span className="approval-time">{left}s left</span>
      </div>

      <h3>{info.label}</h3>
      <p className="approval-summary">
        {stage.status === "staged"
          ? `Approved. It fires in ${left}s unless you cancel — no second tap needed to proceed.`
          : `It ran. You have ${left}s to reverse it.`}
      </p>

      {fields.length > 0 ? (
        <dl className="diff-table">
          {fields.map(([key, value]) => (
            <div className="diff-row" key={key}>
              <dt>{FIELD_LABEL[key] ?? key.replace(/_/g, " ")}</dt>
              <dd>{displayValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="approval-actions">
        {stage.status === "staged" ? (
          <button type="button" className="btn btn-danger" onClick={cancel} disabled={busy}>
            {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="x" size={16} />}
            Cancel before it runs
          </button>
        ) : (
          <button type="button" className="btn btn-primary" onClick={undo} disabled={busy}>
            {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="refresh" size={16} />}
            Undo
          </button>
        )}
      </div>
    </article>
  );
}
