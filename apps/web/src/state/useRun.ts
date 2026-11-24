import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatError, type Task } from "../lib/api";
import { useToast } from "../components/Toast";
import { useData } from "./store";

export interface RunResult {
  task: Task | null;
  taskId: string | null;
  blocked: number;
  error: string | null;
}

/**
 * The only path from this UI to an action: create an authenticated task on the
 * gateway. Observation runs immediately; Execute/Critical steps stop at the
 * approval gate. This hook never calls a connector or an external service.
 */
export function useRunCommand() {
  const [busy, setBusy] = useState(false);
  const { push } = useToast();
  const { refreshCore, refreshSystem } = useData();
  const navigate = useNavigate();

  const run = useCallback(
    async (text: string): Promise<RunResult> => {
      const request = text.trim();
      if (!request) return { task: null, taskId: null, blocked: 0, error: "Say what you need first." };
      setBusy(true);
      try {
        const result = await api.createTask(request);
        const task = (result.task as Task | null) ?? null;
        const blocked = Array.isArray(result.blocked_tools) ? result.blocked_tools.length : 0;
        await Promise.all([refreshCore(), refreshSystem()]);

        if (result.killed) {
          const stopped = Array.isArray(result.stopped) ? result.stopped.length : 0;
          push({
            title: "Emergency stop",
            message:
              stopped > 0
                ? `Kill phrase matched — ${stopped} task${stopped === 1 ? "" : "s"} halted.`
                : "Kill phrase matched — no active tasks, nothing is running.",
            tone: "warn",
          });
          return { task: null, taskId: null, blocked: 0, error: null };
        }

        if (task?.status === "waiting_approval") {
          push({
            title: "Plan ready — waiting on you",
            message: "An Execute or Critical step is holding at the approval gate.",
            tone: "warn",
          });
          navigate("/approvals");
        } else if (task?.status === "failed") {
          const failed = task.steps.find((step) => step.status === "failed");
          push({
            title: "The task ran but failed verification",
            message: failed?.error ?? "Check the exact step record.",
            tone: "bad",
          });
        } else if (task?.status === "completed") {
          push({
            title: "Done and verified",
            message: "The result is in the task record and the audit chain.",
            tone: "ok",
          });
        } else if (blocked > 0) {
          push({
            title: "Some tools were refused",
            message: `${blocked} proposed tool${blocked === 1 ? "" : "s"} were not in the frozen registry.`,
            tone: "warn",
          });
        } else {
          push({ title: "Task created", tone: "info" });
        }
        return { task, taskId: result.task_id ?? null, blocked, error: null };
      } catch (err) {
        const message = formatError(err);
        push({ title: "Could not plan that", message, tone: "bad" });
        return { task: null, taskId: null, blocked: 0, error: message };
      } finally {
        setBusy(false);
      }
    },
    [push, refreshCore, refreshSystem, navigate],
  );

  return { run, busy };
}
