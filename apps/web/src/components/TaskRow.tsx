import type { Task } from "../lib/api";
import { relativeTime, toolInfo } from "../lib/format";
import { useTaskDrawer } from "./TaskDrawer";
import { Icon } from "./Icon";
import { RiskBadge, StatusPill } from "./Pills";

const STATUS_ICON = {
  created: "clock",
  running: "refresh",
  paused: "pause",
  waiting_approval: "shield",
  completed: "check",
  failed: "alert",
  cancelled: "x",
  stopped: "stop",
} as const;

export function TaskRow({ task, showRisk = true }: { task: Task; showRisk?: boolean }) {
  const { openTask } = useTaskDrawer();
  const done = task.steps.filter((step) => step.status === "completed").length;
  const firstTool = task.steps[0]?.tool;
  const info = firstTool ? toolInfo(firstTool) : null;

  return (
    <button type="button" className={`task-row task-tone-${task.status}`} onClick={() => openTask(task.task_id)}>
      <span className={`task-icon task-icon-${task.status}`}>
        <Icon name={STATUS_ICON[task.status]} size={15} />
      </span>
      <span className="task-main">
        <strong>{task.request}</strong>
        <em>
          {info ? info.label : "No tools planned"}
          {task.steps.length > 1 ? ` · ${task.steps.length} steps` : ""}
          {task.steps.length > 0 ? ` · ${done}/${task.steps.length} done` : ""}
        </em>
      </span>
      <span className="task-side">
        {showRisk && task.risk_level ? <RiskBadge tier={task.risk_level} /> : null}
        <StatusPill status={task.status} />
        <span className="task-time">{relativeTime(task.updated_at)}</span>
      </span>
      <Icon name="chevronRight" size={15} className="task-chevron" />
    </button>
  );
}
