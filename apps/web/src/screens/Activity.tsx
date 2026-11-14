import { useMemo, useState } from "react";

import { useData } from "../state/store";
import { actionTone, clockTime, eventLabel, shortId, titleCase, toolInfo } from "../lib/format";
import { PageHeader } from "../components/EmptyState";
import { Icon, type IconName } from "../components/Icon";
import { useToast } from "../components/Toast";
import type { AuditEvent } from "../lib/api";

const FILTERS = [
  { id: "all", label: "Everything", match: () => true },
  {
    id: "consent",
    label: "Consent",
    match: (action: string) => ["requested_approval", "approved", "rejected"].includes(action),
  },
  {
    id: "execution",
    label: "Execution",
    match: (action: string) => ["executed", "verified", "failed"].includes(action),
  },
  {
    id: "tasks",
    label: "Plans",
    match: (action: string) =>
      ["command_received", "command_completed", "planned_step", "state_transition"].includes(action),
  },
  {
    id: "safety",
    label: "Safety",
    match: (action: string) => ["stopped", "cancelled"].includes(action),
  },
] as const;

const ACTION_ICON: Record<string, IconName> = {
  planned_step: "workflow",
  requested_approval: "shield",
  approved: "check",
  rejected: "x",
  executed: "play",
  verified: "check",
  failed: "alert",
  cancelled: "x",
  stopped: "stop",
  command_received: "spark",
  command_completed: "check",
  state_transition: "refresh",
};

function parseDate(iso: string): Date {
  if (!iso) return new Date(NaN);
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
}

function dayKey(iso: string): string {
  const date = parseDate(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function dayTitle(key: string): string {
  if (key === "unknown") return "Undated";
  const [year, month, day] = key.split("-").map(Number);
  const date = new Date(year, month, day);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (key === dayKey(today.toISOString())) return "Today";
  if (key === dayKey(yesterday.toISOString())) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

const HIDDEN_DETAIL = new Set(["tool", "step", "approval", "task"]);

export function Activity() {
  const { events, chainOk, loading } = useData();
  const { push } = useToast();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const active = FILTERS.find((item) => item.id === filter) ?? FILTERS[0];
    const q = query.trim().toLowerCase();
    return events.filter((event) => {
      if (!active.match(event.action)) return false;
      if (!q) return true;
      const tool = typeof event.detail?.tool === "string" ? toolInfo(event.detail.tool).label : "";
      const haystack = [
        event.action,
        event.actor,
        event.task_id ?? "",
        tool,
        JSON.stringify(event.detail ?? {}),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [events, filter, query]);

  const groups = useMemo(() => {
    const map = new Map<string, AuditEvent[]>();
    for (const event of filtered) {
      const key = dayKey(event.created_at);
      const list = map.get(key) ?? [];
      list.push(event);
      map.set(key, list);
    }
    return Array.from(map.entries());
  }, [filtered]);

  const counts = useMemo(() => {
    const verified = events.filter((event) => event.action === "verified").length;
    const failed = events.filter((event) => event.action === "failed").length;
    const actors = new Set(events.map((event) => event.actor));
    return { verified, failed, actors: actors.size };
  }, [events]);

  const copy = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      push({ title: `${label} copied`, tone: "ok" });
    } catch {
      push({ title: "Clipboard unavailable", message: value, tone: "warn" });
    }
  };

  const exportEvents = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `vioris-audit-${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    push({ title: "Audit export prepared", message: `${events.length} events written to a JSON file.`, tone: "ok" });
  };

  return (
    <div className="page activity-page">
      <PageHeader
        eyebrow="Append-only trail"
        title="Every action, chained"
        intro="Each event is hashed with the one before it. Nothing in this view can be edited or removed — the only honest way to read it is top-down."
        actions={
          <>
            <button type="button" className="btn btn-ghost btn-sm" onClick={exportEvents}>
              <Icon name="arrowUpRight" size={14} /> Export JSON
            </button>
            <span className={`page-chip ${chainOk === null ? "" : chainOk ? "is-ok" : "is-bad"}`}>
              <Icon name={chainOk === null ? "clock" : chainOk ? "shield" : "alert"} size={14} />
              {chainOk === null ? "Chain unchecked" : chainOk ? "Chain verified" : "Chain broken"}
            </span>
          </>
        }
      />

      <div className="audit-stats">
        <div>
          <strong>{events.length}</strong>
          <span>events loaded</span>
        </div>
        <div>
          <strong className="is-ok">{counts.verified}</strong>
          <span>verified results</span>
        </div>
        <div>
          <strong className={counts.failed ? "is-bad" : ""}>{counts.failed}</strong>
          <span>failures</span>
        </div>
        <div>
          <strong>{counts.actors}</strong>
          <span>actors</span>
        </div>
        <div className="audit-chain-state">
          <Icon name={chainOk === null ? "clock" : chainOk ? "lock" : "alert"} size={16} />
          <span>
            {chainOk === null
              ? "No chain state yet. The gateway reports it after the first sync."
              : chainOk
                ? "The gateway verified every link before serving this page."
                : "A link failed verification. Treat the trail as suspect and check the task-runner."}
          </span>
        </div>
      </div>

      <div className="audit-toolbar">
        <div className="segmented" role="tablist" aria-label="Filter activity">
          {FILTERS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={filter === item.id}
              className={filter === item.id ? "is-active" : ""}
              onClick={() => setFilter(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <label className="search-field">
          <Icon name="search" size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter by tool, task, actor…"
            aria-label="Filter activity"
          />
        </label>
      </div>

      {loading && events.length === 0 ? (
        <div className="rolling">
          <span className="spinner" /> Reading the audit chain…
        </div>
      ) : groups.length === 0 ? (
        <div className="empty-state">
          <span className="empty-icon">
            <Icon name="activity" size={22} />
          </span>
          <h3>No events match</h3>
          <p>
            {events.length === 0
              ? "The chain is empty. Run an observation and the first link will appear here."
              : "Try a different filter or a broader search."}
          </p>
        </div>
      ) : (
        <div className="audit-days">
          {groups.map(([key, dayEvents]) => (
            <section className="audit-day" key={key}>
              <div className="audit-day-head">
                <h2>{dayTitle(key)}</h2>
                <span>{dayEvents.length} events</span>
              </div>
              <ol className="audit-list">
                {dayEvents.map((event) => {
                  const tone = actionTone(event.action);
                  const icon = ACTION_ICON[event.action] ?? "activity";
                  const detail = event.detail ?? {};
                  const tool = typeof detail.tool === "string" ? detail.tool : null;
                  const extra = Object.entries(detail).filter(([detailKey]) => !HIDDEN_DETAIL.has(detailKey));
                  return (
                    <li className={`audit-item tone-${tone}`} key={event.hash ?? `${event.created_at}-${event.action}`}>
                      <span className="audit-marker">
                        <Icon name={icon} size={14} />
                      </span>
                      <div className="audit-content">
                        <div className="audit-top">
                          <strong>{eventLabel(event.action, detail)}</strong>
                          <span className="audit-time">{clockTime(event.created_at)}</span>
                        </div>
                        <div className="audit-chips">
                          <span className="actor-chip">{event.actor}</span>
                          {tool ? <code className="tool-chip">{tool}</code> : null}
                          {event.task_id ? (
                            <button
                              type="button"
                              className="hash-chip"
                              title="Copy task id"
                              onClick={() => void copy(event.task_id ?? "", "Task id")}
                            >
                              task {shortId(event.task_id)}
                            </button>
                          ) : null}
                          {extra.map(([key, value]) => (
                            <span className="detail-chip" key={key}>
                              {titleCase(key)}: {typeof value === "object" ? JSON.stringify(value) : String(value)}
                            </span>
                          ))}
                        </div>
                        <details className="audit-detail">
                          <summary>
                            Event hash <code>{event.hash ? `${event.hash.slice(0, 12)}…` : "—"}</code>
                          </summary>
                          <div className="hash-block">
                            <span>prev</span>
                            <code>{event.prev_hash ?? "—"}</code>
                            <span>hash</span>
                            <code>{event.hash ?? "—"}</code>
                            <button
                              type="button"
                              className="btn btn-ghost btn-xs"
                              onClick={() => void copy(JSON.stringify(event.detail ?? {}, null, 2), "Detail")}
                            >
                              Copy detail
                            </button>
                          </div>
                        </details>
                      </div>
                    </li>
                  );
                })}
              </ol>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
