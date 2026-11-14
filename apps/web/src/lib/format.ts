import type { RiskTier, TaskStatus } from "./api";

export const RISK_LABEL: Record<RiskTier, string> = {
  observe: "Observe",
  prepare: "Prepare",
  execute: "Execute",
  critical: "Critical",
};

export const RISK_COPY: Record<RiskTier, string> = {
  observe: "Read-only. Runs without asking.",
  prepare: "Stages a change without applying it.",
  execute: "Needs your explicit approval and an exact diff.",
  critical: "Approval, a local PIN, and a cooldown.",
};

export const STATUS_LABEL: Record<TaskStatus, string> = {
  created: "Queued",
  running: "Running",
  paused: "Paused",
  waiting_approval: "Waiting on you",
  completed: "Verified",
  failed: "Failed",
  cancelled: "Cancelled",
  stopped: "Stopped",
};

interface ToolInfo {
  label: string;
  blurb: string;
  service: string;
}

export const TOOL_INFO: Record<string, ToolInfo> = {
  "system.get_time": { label: "Read the clock", blurb: "Local time and date.", service: "system" },
  "system.send_message": { label: "Send a message", blurb: "Delivers through a connected transport after approval.", service: "whatsapp" },
  "system.set_reminder": { label: "Set a reminder", blurb: "Writes a local reminder entry.", service: "system" },
  "system.stop": { label: "Stop everything", blurb: "Halts every non-terminal task.", service: "system" },
  "system.register_payment": { label: "Register a payment", blurb: "Payment registration is locked in this build.", service: "system" },
  "gmail.read_unread": { label: "Read unread mail", blurb: "Fetches unread messages from Gmail.", service: "gmail" },
  "calendar.read_upcoming": { label: "Read the calendar", blurb: "Lists upcoming events.", service: "calendar" },
  "whatsapp.read_digest": { label: "Read WhatsApp digest", blurb: "Summarizes recent messages.", service: "whatsapp" },
  "whatsapp.send_message": { label: "Send WhatsApp message", blurb: "Sends once you approve the exact text.", service: "whatsapp" },
  "contacts.search": { label: "Search contacts", blurb: "Finds people in your address book.", service: "contacts" },
  "reservations.search_slots": { label: "Search reservation slots", blurb: "Reads availability for a venue.", service: "reservations" },
  "reservations.create": { label: "Book a reservation", blurb: "Books a table after approval.", service: "reservations" },
  "smart_home.device_status": { label: "Read device status", blurb: "Checks smart-home state.", service: "smart home" },
  "smart_home.control": { label: "Control a device", blurb: "Changes a device after approval.", service: "smart home" },
  "computer.start_remote_session": { label: "Start remote session", blurb: "Opens a time-boxed control session.", service: "computer" },
  "computer.capture_screenshot": { label: "Capture screen", blurb: "Takes one screen frame.", service: "computer" },
  "notes.read_recent": { label: "Read recent notes", blurb: "Lists recent notes.", service: "notes" },
  "bookmarks.search": { label: "Search bookmarks", blurb: "Finds saved links.", service: "bookmarks" },
  "cloud_files.read_recent": { label: "Read recent files", blurb: "Lists recent documents.", service: "cloud files" },
  "history.read_recent": { label: "Read recent pages", blurb: "Lists browser history.", service: "history" },
  "telephony.prepare_call": { label: "Prepare a call", blurb: "Stages a call; never auto-dials.", service: "telephony" },
};

export function toolInfo(tool: string): ToolInfo {
  return (
    TOOL_INFO[tool] ?? {
      label: tool.replace(/[._]/g, " "),
      blurb: "Registered tool.",
      service: tool.split(".")[0] ?? "tool",
    }
  );
}

/**
 * Accept ISO strings from the API, plus epoch seconds/ms defensively.
 * A malformed value yields an invalid Date rather than throwing.
 */
export function parseTimestamp(value?: string | number | null): Date {
  if (value === null || value === undefined || value === "") return new Date(NaN);
  if (typeof value === "number") {
    return new Date(value > 1e12 ? value : value * 1000);
  }
  if (typeof value !== "string") return new Date(NaN);
  const iso = value.trim();
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
}

export function relativeTime(iso?: string | number | null): string {
  if (!iso) return "—";
  const then = parseTimestamp(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const delta = Date.now() - then;
  if (delta < 0) {
    const seconds = Math.round(-delta / 1000);
    if (seconds < 60) return `in ${seconds}s`;
    const minutes = Math.round(seconds / 60);
    return `in ${minutes}m`;
  }
  const seconds = Math.round(delta / 1000);
  if (seconds < 8) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(then).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function clockTime(iso?: string | number | null): string {
  if (!iso) return "—";
  const then = parseTimestamp(iso);
  if (Number.isNaN(then.getTime())) return "—";
  return then.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function shortId(id?: string | null): string {
  if (!id) return "—";
  const cleaned = id.replace(/^(task_|step_|apr_|evt_|mem_|dev_)/, "");
  return cleaned.length > 8 ? cleaned.slice(0, 8) : cleaned;
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function greeting(date = new Date()): string {
  const hour = date.getHours();
  if (hour < 5) return "Still up";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export function dayLabel(date = new Date()): string {
  return date.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function actionTone(action: string): "ok" | "warn" | "bad" | "info" | "muted" {
  if (["verified", "command_completed", "approved"].includes(action)) return "ok";
  if (["requested_approval", "planned_step", "state_transition"].includes(action)) return "warn";
  if (["failed", "rejected", "stopped", "cancelled"].includes(action)) return "bad";
  if (["executed", "command_received"].includes(action)) return "info";
  return "muted";
}

export function eventLabel(action: string, detail: Record<string, unknown> = {}): string {
  const tool = typeof detail.tool === "string" ? detail.tool : null;
  const name = tool ? toolInfo(tool).label : null;
  switch (action) {
    case "planned_step":
      return name ? `Planned ${name.toLowerCase()}` : "Planned a step";
    case "requested_approval":
      return name ? `Asked to ${name.toLowerCase()}` : "Requested approval";
    case "approved":
      return "You approved an action";
    case "rejected":
      return "You rejected an action";
    case "executed":
      return name ? `Executed ${name.toLowerCase()}` : "Executed a step";
    case "verified":
      return "Result verified";
    case "failed":
      return name ? `${name} failed` : "A step failed";
    case "cancelled":
      return "Task cancelled";
    case "stopped":
      return "Everything stopped";
    case "command_received":
      return "Request received";
    case "command_completed":
      return "Request completed";
    case "state_transition":
      return "State changed";
    default:
      return titleCase(action);
  }
}
