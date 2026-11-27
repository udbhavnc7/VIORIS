import { useMemo } from "react";
import { Link } from "react-router-dom";

import { useData } from "../state/store";
import { relativeTime, shortId, toolInfo } from "../lib/format";
import { ApprovalCard } from "../components/ApprovalCard";
import { EmptyState, PageHeader } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { RiskBadge } from "../components/Pills";
import { StageCard } from "../components/StageCard";
import type { RiskTier } from "../lib/api";

const TIERS: RiskTier[] = ["observe", "prepare", "execute", "critical"];

export function Approvals() {
  const { approvals, stages, events, chainOk, system } = useData();
  const liveStages = stages.filter(
    (stage) => stage.status === "staged" || (stage.status === "executed" && stage.undoable),
  );

  const decisions = useMemo(
    () => events.filter((event) => ["approved", "rejected"].includes(event.action)).slice(0, 10),
    [events],
  );

  return (
    <div className="page approvals-page">
      <PageHeader
        eyebrow="Consent gate"
        title={approvals.length > 0 ? `${approvals.length} action${approvals.length === 1 ? "" : "s"} waiting` : "Nothing is waiting"}
        intro="Every Execute and Critical step stops here with the exact destination, text, and consequence. Approving is a deliberate act — the button never says “OK”."
        actions={
          <span className={`page-chip ${chainOk === null ? "" : chainOk ? "is-ok" : "is-bad"}`}>
            <Icon name={chainOk === null ? "clock" : chainOk ? "shield" : "alert"} size={14} />
            {chainOk === null
              ? "Audit chain unchecked"
              : chainOk
                ? "Audit chain verified"
                : "Audit chain needs attention"}
          </span>
        }
      />

      <div className="approvals-layout">
        <div className="approvals-main">
          {liveStages.length > 0 ? (
            <section className="panel">
              <div className="section-head">
                <div>
                  <h2>In motion</h2>
                  <p>
                    Approved Critical actions wait out a cancel window before they fire, then stay
                    reversible for a few minutes where an undo exists.
                  </p>
                </div>
              </div>
              <div className="approval-stack">
                {liveStages.map((stage) => (
                  <StageCard key={stage.stage_id} stage={stage} />
                ))}
              </div>
            </section>
          ) : null}

          {approvals.length === 0 ? (
            <EmptyState
              icon="shield"
              title="All clear"
              body="No action is holding. When a step needs you, it appears here with its exact diff — nothing fires from a notification tap alone."
              action={
                <Link to="/workflows" className="btn btn-primary">
                  <Icon name="workflow" size={16} /> Run a workflow
                </Link>
              }
            />
          ) : (
            <div className="approval-stack">
              {approvals.map((approval) => (
                <ApprovalCard key={approval.approval_id} approval={approval} />
              ))}
            </div>
          )}

          <section className="panel">
            <div className="section-head">
              <div>
                <h2>Recent decisions</h2>
                <p>Reconstructed from the audit chain — approving and rejecting are both recorded.</p>
              </div>
              <Link to="/activity" className="text-link">
                Full trail <Icon name="chevronRight" size={13} />
              </Link>
            </div>
            {decisions.length === 0 ? (
              <p className="rail-empty">No approvals have been decided yet.</p>
            ) : (
              <ol className="decision-list">
                {decisions.map((event, index) => {
                  const detail = event.detail ?? {};
                  const tool = typeof detail.tool === "string" ? detail.tool : null;
                  const approvalId =
                    typeof detail.approval === "string" ? detail.approval : null;
                  return (
                    <li key={event.hash ?? `decision-${index}`} className={`decision decision-${event.action}`}>
                      <span className="decision-icon">
                        <Icon name={event.action === "approved" ? "check" : "x"} size={14} />
                      </span>
                      <div className="decision-copy">
                        <strong>
                          {event.action === "approved" ? "Approved" : "Rejected"}
                          {tool ? ` · ${toolInfo(tool).label}` : ""}
                        </strong>
                        <span>
                          {approvalId ? `approval ${shortId(approvalId)} · ` : ""}
                          {event.actor} · {relativeTime(event.created_at)}
                        </span>
                      </div>
                      <span className={`decision-tag tone-${event.action === "approved" ? "ok" : "bad"}`}>
                        {event.action}
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>
        </div>

        <aside className="approvals-rail">
          <section className="panel">
            <div className="section-head">
              <div>
                <h2>The four tiers</h2>
                <p>Chosen by the frozen registry, never by the model.</p>
              </div>
            </div>
            <div className="tier-legend">
              {TIERS.map((tier, index) => (
                <div className="tier-row" key={tier}>
                  <span className="tier-index">0{index + 1}</span>
                  <div>
                    <RiskBadge tier={tier} />
                    <p>
                      {tier === "observe" && "Reads run immediately. No consent needed, still audited."}
                      {tier === "prepare" && "Stages a change but never applies it. Shown, not sent."}
                      {tier === "execute" && "Full approval with an exact diff before anything fires."}
                      {tier === "critical" && "Approval, a local PIN, and a cooldown. Locked if no PIN is set."}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="section-head">
              <div>
                <h2>Critical posture</h2>
                <p>Read from this machine right now.</p>
              </div>
            </div>
            <div className={`posture ${system?.critical_actions === "unlocked" ? "is-ok" : "is-warn"}`}>
              <span className="posture-icon">
                <Icon name={system?.critical_actions === "unlocked" ? "lockOpen" : "lock"} size={20} />
              </span>
              <div>
                <strong>
                  {system?.critical_actions === "unlocked" ? "Armed" : "Locked"}
                </strong>
                <p>
                  {system?.critical_actions === "unlocked"
                    ? "A local PIN is configured. Critical approvals also wait out a five-second cooldown."
                    : system?.critical_note ?? "Set VIORIS_CRITICAL_PIN on the gateway host to arm Critical actions."}
                </p>
              </div>
            </div>
            <p className="fine-print">
              The proof is minted for one approval id, expires quickly, and is verified again by the
              task-runner. A direct API call cannot skip the gate.
            </p>
          </section>

          <section className="panel panel-quiet">
            <div className="section-head">
              <div>
                <h2>What approval is not</h2>
              </div>
            </div>
            <ul className="plain-list">
              <li>It is not a blanket “allow everything” switch.</li>
              <li>It does not survive a changed recipient or message.</li>
              <li>It is not inferred from a previous approval.</li>
              <li>It is not hidden — the diff is the consent.</li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}
