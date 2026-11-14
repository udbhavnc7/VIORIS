import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, formatError, type Task, type TaskStep } from "../lib/api";
import { relativeTime, shortId, toolInfo } from "../lib/format";
import { useData } from "../state/store";
import { ApprovalCard } from "./ApprovalCard";
import { Icon } from "./Icon";
import { RiskBadge, StatusPill } from "./Pills";
import { useToast } from "./Toast";

interface DrawerApi {
  openTask: (taskId: string) => void;
  closeTask: () => void;
}

const DrawerContext = createContext<DrawerApi | null>(null);

const STEP_ICON = {
  created: "clock",
  running: "refresh",
  paused: "pause",
  waiting_approval: "shield",
  completed: "check",
  failed: "alert",
  cancelled: "x",
  stopped: "stop",
} as const;

function pretty(value: unknown): string {
  if (value === null || value === undefined) return "—";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function StepRow({ step }: { step: TaskStep }) {
  const info = toolInfo(step.tool);
  return (
    <li className={`step-row step-${step.status}`}>
      <span className="step-marker">
        <Icon name={STEP_ICON[step.status]} size={15} />
      </span>
      <div className="step-body">
        <div className="step-top">
          <strong>{info.label}</strong>
          <StatusPill status={step.status} />
        </div>
        <div className="step-sub">
          <code>{step.tool}</code>
          <RiskBadge tier={step.risk_level} />
          <span>{relativeTime(step.created_at)}</span>
        </div>
        {step.error ? <p className="step-error">{step.error}</p> : null}
        {step.result ? (
          <details className="step-result">
            <summary>Exact result</summary>
            <pre>{pretty(step.result)}</pre>
          </details>
        ) : null}
      </div>
    </li>
  );
}

export function TaskDrawerProvider({ children }: { children: ReactNode }) {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { approvals, refreshCore } = useData();
  const { push } = useToast();

  const closeTask = useCallback(() => {
    setTaskId(null);
    setTask(null);
    setError(null);
  }, []);

  const openTask = useCallback((id: string) => {
    setTaskId(id);
    setTask(null);
    setError(null);
  }, []);

  const load = useCallback(async (id: string) => {
    try {
      setTask(await api.task(id));
    } catch (err) {
      setError(formatError(err));
    }
  }, []);

  useEffect(() => {
    if (!taskId) return;
    void load(taskId);
  }, [taskId, load]);

  useEffect(() => {
    if (!taskId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeTask();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [taskId, closeTask]);

  useEffect(() => {
    if (!taskId || !task) return;
    if (["completed", "failed", "cancelled", "stopped"].includes(task.status)) return;
    const timer = window.setInterval(() => void load(taskId), 4000);
    return () => window.clearInterval(timer);
  }, [taskId, task, load]);

  const act = async (label: string, action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      if (taskId) await load(taskId);
      await refreshCore();
      push({ title: label, tone: "ok" });
    } catch (err) {
      push({ title: `Could not ${label.toLowerCase()}`, message: formatError(err), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  const value = useMemo(() => ({ openTask, closeTask }), [openTask, closeTask]);
  const linked = task ? approvals.filter((approval) => approval.task_id === task.task_id) : [];
  const open = Boolean(taskId);

  return (
    <DrawerContext.Provider value={value}>
      {children}
      <div className={`drawer-backdrop ${open ? "is-open" : ""}`} onClick={closeTask} aria-hidden={!open} />
      <aside
        className={`task-drawer ${open ? "is-open" : ""}`}
        aria-hidden={!open}
        aria-label="Task detail"
      >
        {open ? (
          <>
            <header className="drawer-head">
              <div>
                <span className="eyebrow">Task {taskId ? shortId(taskId) : ""}</span>
                <h2>{task?.request ?? "Loading task…"}</h2>
              </div>
              <button type="button" className="icon-btn" aria-label="Close task" onClick={closeTask}>
                <Icon name="x" size={18} />
              </button>
            </header>

            <div className="drawer-body">
              {error ? (
                <div className="callout callout-bad">
                  <Icon name="alert" size={16} />
                  <div>
                    <strong>Could not load this task</strong>
                    <span>{error}</span>
                  </div>
                </div>
              ) : null}

              {task ? (
                <>
                  <div className="task-facts">
                    <div>
                      <span>Status</span>
                      <StatusPill status={task.status} />
                    </div>
                    <div>
                      <span>Steps</span>
                      <strong>
                        {task.steps.filter((s) => s.status === "completed").length}/{task.steps.length}
                      </strong>
                    </div>
                    <div>
                      <span>Updated</span>
                      <strong>{relativeTime(task.updated_at)}</strong>
                    </div>
                  </div>

                  <div className="drawer-actions">
                    {["created", "running"].includes(task.status) ? (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        disabled={busy}
                        onClick={() => void act("Paused task", () => api.pauseTask(task.task_id))}
                      >
                        <Icon name="pause" size={14} /> Pause
                      </button>
                    ) : null}
                    {task.status === "paused" ? (
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        disabled={busy}
                        onClick={() => void act("Resumed task", () => api.resumeTask(task.task_id))}
                      >
                        <Icon name="play" size={14} /> Resume
                      </button>
                    ) : null}
                    {task.status === "waiting_approval" ? (
                      <span className="drawer-hint">
                        <Icon name="shield" size={14} /> Waiting for your decision below
                      </span>
                    ) : null}
                    {!["completed", "failed", "cancelled", "stopped"].includes(task.status) ? (
                      <button
                        type="button"
                        className="btn btn-danger-ghost btn-sm"
                        disabled={busy}
                        onClick={() => void act("Cancelled task", () => api.cancelTask(task.task_id))}
                      >
                        <Icon name="stop" size={14} /> Cancel
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => void load(task.task_id)}
                    >
                      <Icon name="refresh" size={14} /> Refresh
                    </button>
                  </div>

                  {linked.length > 0 ? (
                    <section className="drawer-section">
                      <h3>Approval holding this task</h3>
                      {linked.map((approval) => (
                        <ApprovalCard
                          key={approval.approval_id}
                          approval={approval}
                          onDecided={() => void load(task.task_id)}
                        />
                      ))}
                    </section>
                  ) : null}

                  <section className="drawer-section">
                    <h3>Steps</h3>
                    <ol className="step-list">
                      {task.steps.map((step) => (
                        <StepRow key={step.step_id} step={step} />
                      ))}
                    </ol>
                  </section>

                  {task.steps.some((step) => step.status === "failed") ? (
                    <section className="drawer-section">
                      <h3>Recover</h3>
                      <p className="fine-print">
                        A retry keeps the same consent binding. Vioris re-checks the approval record
                        before it fires again.
                      </p>
                      {task.steps
                        .filter((step) => step.status === "failed")
                        .map((step) => (
                          <button
                            key={step.step_id}
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={busy}
                            onClick={() =>
                              void act("Retried step", () =>
                                api.retryStep(task.task_id, step.step_id),
                              )
                            }
                          >
                            <Icon name="refresh" size={14} /> Retry {step.tool}
                          </button>
                        ))}
                    </section>
                  ) : null}
                </>
              ) : (
                <div className="drawer-loading">
                  <span className="spinner" aria-hidden="true" />
                  Loading the exact record…
                </div>
              )}
            </div>
          </>
        ) : null}
      </aside>
    </DrawerContext.Provider>
  );
}

export function useTaskDrawer(): DrawerApi {
  const context = useContext(DrawerContext);
  if (!context) throw new Error("useTaskDrawer must be used inside TaskDrawerProvider");
  return context;
}
