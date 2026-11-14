import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useData } from "../state/store";
import { useRunCommand } from "../state/useRun";
import { useNow } from "../hooks/useNow";
import { actionTone, clockTime, dayLabel, eventLabel, greeting, relativeTime } from "../lib/format";
import { ApprovalCard } from "../components/ApprovalCard";
import { Composer } from "../components/Composer";
import { Icon } from "../components/Icon";
import { Orb, type OrbState } from "../components/Orb";
import { LiveDot } from "../components/Pills";
import { SectionHead } from "../components/EmptyState";
import { TaskRow } from "../components/TaskRow";
import { useTaskDrawer } from "../components/TaskDrawer";

export function Overview() {
  const {
    tasks,
    approvals,
    events,
    chainOk,
    connections,
    system,
    health,
    connected,
    loading,
    lastSync,
  } = useData();
  const { run, busy } = useRunCommand();
  const { openTask } = useTaskDrawer();
  const now = useNow(1000);

  const activeTasks = useMemo(
    () => tasks.filter((task) => ["created", "running", "paused", "waiting_approval"].includes(task.status)),
    [tasks],
  );
  const running = activeTasks.filter((task) => task.status === "running").length;
  const waitingTasks = tasks.filter((task) => task.status === "waiting_approval").length;

  const orbState: OrbState = useMemo(() => {
    if (!health) return "error";
    if (running > 0) return "executing";
    if (approvals.length > 0) return "awaiting";
    return "idle";
  }, [health, running, approvals.length]);

  const [briefBusy, setBriefBusy] = useState(false);

  const statusLine = useMemo(() => {
    if (!health) return "The local gateway is not answering. Start it and this page will rejoin.";
    if (approvals.length > 0) {
      return `${approvals.length} action${approvals.length === 1 ? " is" : "s are"} holding at the approval gate. Nothing fires until you decide.`;
    }
    if (running > 0) {
      return `${running} task${running === 1 ? " is" : "s are"} running through verified steps.`;
    }
    return "Nothing is holding. Ask for a briefing, check mail, or open a workflow.";
  }, [health, approvals.length, running]);

  const brief = async () => {
    setBriefBusy(true);
    await run("Brief me");
    setBriefBusy(false);
  };

  const recentEvents = events.slice(0, 6);
  const configured = connections?.configured_count ?? 0;
  const totalConnections = connections?.total ?? 0;

  return (
    <div className="page overview-page">
      <section className="hero-panel">
        <div className="hero-orb">
          <Orb state={orbState} />
        </div>
        <div className="hero-copy">
          <div className="hero-topline">
            <span className="eyebrow">{dayLabel()}</span>
            <span className="hero-clock">
              {clockTime(new Date(now).toISOString())}
              <em>local</em>
            </span>
          </div>
          <h1>
            {greeting()}.
            <br />
            <span className="hero-sub">The house is quiet.</span>
          </h1>
          <p className="hero-status">{statusLine}</p>

          <div className="hero-actions">
            {approvals.length > 0 ? (
              <Link to="/approvals" className="btn btn-primary">
                <Icon name="shield" size={16} />
                Review {approvals.length} approval{approvals.length === 1 ? "" : "s"}
              </Link>
            ) : (
              <Link to="/workflows" className="btn btn-primary">
                <Icon name="workflow" size={16} />
                Open workflows
              </Link>
            )}
            <button type="button" className="btn btn-ghost" onClick={() => void brief()} disabled={briefBusy || busy}>
              {briefBusy || busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="spark" size={16} />}
              Run a briefing
            </button>
            <Link to="/call" className="btn btn-ghost">
              <Icon name="phone" size={16} />
              Place a test call
            </Link>
          </div>

          <div className="hero-metrics">
            <div>
              <strong>{system?.tools_registered ?? "—"}</strong>
              <span>tools frozen</span>
            </div>
            <div>
              <strong>{configured}</strong>
              <span>of {totalConnections || "—"} reachable</span>
            </div>
            <div>
              <strong className={chainOk === null ? "" : chainOk ? "is-ok" : "is-bad"}>
                {chainOk === null ? "—" : chainOk ? "Intact" : "Broken"}
              </strong>
              <span>audit chain</span>
            </div>
            <div>
              <strong>{connected?.total ?? 0}</strong>
              <span>sockets</span>
            </div>
          </div>
        </div>
      </section>

      <Composer variant="hero" />

      <div className="stat-strip">
        <div className="stat-card">
          <span className="stat-icon tone-amber">
            <Icon name="shield" size={17} />
          </span>
          <div>
            <strong>{approvals.length}</strong>
            <span>waiting on you</span>
          </div>
          <Link to="/approvals" aria-label="Open approvals">
            <Icon name="chevronRight" size={15} />
          </Link>
        </div>
        <div className="stat-card">
          <span className="stat-icon tone-cyan">
            <Icon name="activity" size={17} />
          </span>
          <div>
            <strong>{activeTasks.length}</strong>
            <span>active tasks</span>
          </div>
          <span className="stat-note">{waitingTasks > 0 ? `${waitingTasks} gated` : "none gated"}</span>
        </div>
        <div className="stat-card">
          <span className="stat-icon tone-violet">
            <Icon name="plug" size={17} />
          </span>
          <div>
            <strong>{configured}</strong>
            <span>connections up</span>
          </div>
          <Link to="/connections" aria-label="Open connections">
            <Icon name="chevronRight" size={15} />
          </Link>
        </div>
        <div className="stat-card">
          <span className={`stat-icon ${chainOk === null ? "" : chainOk ? "tone-green" : "tone-red"}`}>
            <Icon name="terminal" size={17} />
          </span>
          <div>
            <strong>{events.length}</strong>
            <span>audit events</span>
          </div>
          <span className={`stat-note ${chainOk === null ? "" : chainOk ? "is-ok" : "is-bad"}`}>
            {chainOk === null ? "checking" : chainOk ? "verified" : "check"}
          </span>
        </div>
      </div>

      <div className="overview-grid">
        <div className="overview-main">
          <section className="panel">
            <SectionHead
              title="Waiting on you"
              note="Risky actions stop here. The diff is exact — nothing is hidden behind a summary."
              action={
                approvals.length > 0 ? (
                  <Link to="/approvals" className="text-link">
                    All approvals <Icon name="chevronRight" size={13} />
                  </Link>
                ) : null
              }
            />
            {approvals.length > 0 ? (
              <div className="approval-stack">
                {approvals.slice(0, 2).map((approval) => (
                  <ApprovalCard key={approval.approval_id} approval={approval} compact />
                ))}
              </div>
            ) : (
              <div className="clear-card">
                <span className="clear-icon">
                  <Icon name="shield" size={20} />
                </span>
                <div>
                  <strong>All clear</strong>
                  <p>
                    No action is waiting. Vioris will stop the moment a step needs your explicit
                    consent.
                  </p>
                </div>
                <Link to="/activity" className="btn btn-ghost btn-sm">
                  View trail
                </Link>
              </div>
            )}
          </section>

          <section className="panel">
            <SectionHead
              title="Recent tasks"
              note="Plans, live progress, and the exact result of every step."
              action={
                <Link to="/workflows" className="text-link">
                  New request <Icon name="plus" size={13} />
                </Link>
              }
            />
            {loading && tasks.length === 0 ? (
              <div className="rolling">
                <span className="spinner" /> Loading the task ledger…
              </div>
            ) : tasks.length === 0 ? (
              <div className="clear-card">
                <span className="clear-icon">
                  <Icon name="spark" size={20} />
                </span>
                <div>
                  <strong>No tasks yet</strong>
                  <p>Start with “Brief me” above, or open a workflow that is safe to run.</p>
                </div>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => void brief()}>
                  Run a briefing
                </button>
              </div>
            ) : (
              <div className="task-list">
                {tasks.slice(0, 6).map((task) => (
                  <TaskRow key={task.task_id} task={task} />
                ))}
              </div>
            )}
          </section>
        </div>

        <aside className="overview-rail">
          <section className="panel rail-panel">
            <SectionHead title="Runtime" note="Honest state, read from the gateway." />
            <div className="runtime-list">
              <div className="runtime-row">
                <span className="runtime-icon">
                  <Icon name="lock" size={15} />
                </span>
                <div>
                  <strong>Registry</strong>
                  <span>
                    {system?.registry_frozen
                      ? `${system.tools_registered} tools frozen at startup`
                      : "Not frozen — check the gateway"}
                  </span>
                </div>
                <span className={`mini-status ${system?.registry_frozen ? "is-ok" : "is-bad"}`}>
                  {system?.registry_frozen ? "fixed" : "open"}
                </span>
              </div>
              <div className="runtime-row">
                <span className="runtime-icon">
                  <Icon name={system?.critical_actions === "unlocked" ? "lockOpen" : "lock"} size={15} />
                </span>
                <div>
                  <strong>Critical actions</strong>
                  <span>
                    {system?.critical_actions === "unlocked"
                      ? "PIN set · cooldown enforced"
                      : system?.critical_note ?? "Locked until a local PIN exists"}
                  </span>
                </div>
                <span
                  className={`mini-status ${system?.critical_actions === "unlocked" ? "is-ok" : "is-warn"}`}
                >
                  {system?.critical_actions === "unlocked" ? "armed" : "locked"}
                </span>
              </div>
              <div className="runtime-row">
                <span className="runtime-icon">
                  <Icon name={chainOk === null ? "clock" : chainOk ? "shield" : "alert"} size={15} />
                </span>
                <div>
                  <strong>Audit chain</strong>
                  <span>
                    {chainOk === null
                      ? "Waiting for the first sync"
                      : chainOk
                        ? "Every event links to the one before it"
                        : "A link does not verify"}
                  </span>
                </div>
                <span className={`mini-status ${chainOk === null ? "" : chainOk ? "is-ok" : "is-bad"}`}>
                  {chainOk === null ? "—" : chainOk ? "ok" : "fail"}
                </span>
              </div>
              <div className="runtime-row">
                <span className="runtime-icon">
                  <Icon name={connected?.total ? "wifi" : "wifiOff"} size={15} />
                </span>
                <div>
                  <strong>Device sockets</strong>
                  <span>
                    {connected
                      ? `${connected.phones} phone · ${connected.laptops} laptop · ${connected.total} total`
                      : "No live socket"}
                  </span>
                </div>
                <LiveDot live={Boolean(connected?.total)} />
              </div>
            </div>
            <Link to="/settings" className="rail-link">
              Open security settings <Icon name="arrowUpRight" size={13} />
            </Link>
          </section>

          <section className="panel rail-panel">
            <SectionHead
              title="Latest signal"
              note="The tail of the audit chain, newest first."
              action={
                <Link to="/activity" className="text-link">
                  Full trail
                </Link>
              }
            />
            {recentEvents.length === 0 ? (
              <p className="rail-empty">No audit events yet. Every action will land here.</p>
            ) : (
              <ol className="signal-list">
                {recentEvents.map((event, index) => (
                  <li key={event.hash ?? `${event.action}-${index}`} className={`signal tone-${actionTone(event.action)}`}>
                    <span className="signal-dot" aria-hidden="true" />
                    <div>
                      <strong>{eventLabel(event.action, event.detail ?? {})}</strong>
                      <span>
                        {event.actor} · {relativeTime(event.created_at)}
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="panel rail-panel rail-tasks">
            <SectionHead title="Open a task" note="The most recent live record." />
            {activeTasks.length === 0 ? (
              <p className="rail-empty">No live tasks. Recent finished work is in the list on the left.</p>
            ) : (
              <div className="rail-task-list">
                {activeTasks.slice(0, 3).map((task) => (
                  <button
                    key={task.task_id}
                    type="button"
                    className="rail-task"
                    onClick={() => openTask(task.task_id)}
                  >
                    <span>{task.request}</span>
                    <em>
                      {task.status.replace("_", " ")} · {relativeTime(task.updated_at)}
                    </em>
                  </button>
                ))}
              </div>
            )}
            <p className="fine-print">
              Last sync {lastSync ? relativeTime(new Date(lastSync).toISOString()) : "—"}.
            </p>
            <Link to="/remote" className="rail-link">
              {health ? "This laptop is reachable through a gated session" : "Gateway offline"}
              <Icon name="arrowUpRight" size={13} />
            </Link>
          </section>
        </aside>
      </div>

      {!health ? (
        <div className="offline-banner">
          <Icon name="wifiOff" size={16} />
          <div>
            <strong>The gateway is offline.</strong>
            <span>
              Run <code>python run_vioris.py</code> in the project root, then reload. No action will be
              simulated in the meantime.
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
