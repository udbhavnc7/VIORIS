/** Typed client for the authenticated gateway, reached through the local UI host. */

export type RiskTier = "observe" | "prepare" | "execute" | "critical";

export type TaskStatus =
  | "created"
  | "running"
  | "paused"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "stopped";

export interface ReasoningAlternative {
  tool?: string;
  reason?: string;
  rejected?: boolean;
}

export interface TaskStep {
  step_id: string;
  agent: string;
  tool: string;
  risk_level: RiskTier;
  status: TaskStatus;
  error: string | null;
  result: unknown;
  reasoning?: string | null;
  alternatives?: ReasoningAlternative[];
  created_at: string;
}

export interface StepReasoning {
  step_id: string;
  tool: string;
  chosen_reason: string;
  alternatives: ReasoningAlternative[];
  risk_rationale: string;
  user_request: string;
  created_at: string;
  source?: "recorded" | "derived";
}

export interface TaskExplanation {
  task_id: string;
  request: string;
  status: TaskStatus;
  explanation: string;
  steps: StepReasoning[];
  found: boolean;
}

export interface WhyAnswer {
  query: string;
  explanation: string;
  found: boolean;
  query_type: string;
}

export interface Task {
  task_id: string;
  request: string;
  status: TaskStatus;
  risk_level: RiskTier | null;
  steps: TaskStep[];
  created_at: string;
  updated_at: string;
}

export interface Approval {
  approval_id: string;
  task_id: string;
  step_id: string;
  tool: string;
  diff_card: Record<string, unknown>;
  status: string;
  created_at: string;
  risk_level?: RiskTier;
}

export interface Stage {
  stage_id: string;
  tool: string;
  task_id: string;
  step_id: string;
  description: string;
  status: "staged" | "executed" | "cancelled" | "undone";
  diff_card: Record<string, unknown>;
  created_at: string;
  execute_after_seconds: number;
  undo_window_seconds: number;
  undoable: boolean;
}

export interface AuditEvent {
  event_id?: string;
  task_id?: string | null;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  prev_hash?: string | null;
  hash?: string;
  created_at: string;
}

export interface MemoryItem {
  memory_id: string;
  category: "preferences" | "people" | "projects" | "routines" | "sensitive";
  content: string;
  source: string;
  confidence: number;
  user_opted_in: boolean;
  created_at: string;
  updated_at: string;
}

export type ConnectionMode = "demo" | "live" | "off" | "local";

export interface Connection {
  id: string;
  name: string;
  category: "communication" | "knowledge" | "actions" | "local";
  configured: boolean;
  mode: ConnectionMode;
  detail: string;
  capabilities: string[];
  note: string;
}

export interface ConnectionsResponse {
  demo_mode: boolean;
  connections: Connection[];
  configured_count: number;
  total: number;
}

export interface ServiceState {
  reachable: boolean;
  detail: string;
}

export interface SystemStatus {
  registry_frozen: boolean;
  tools_registered: number;
  demo_mode: boolean;
  critical_actions: "unlocked" | "locked";
  critical_note: string;
  local_only: boolean;
  task_runner: string;
  memory_service: string;
  computer_agent: string;
  task_runner_state?: ServiceState;
  memory_state?: ServiceState;
  computer_agent_state?: ServiceState;
}

export interface GatewayHealth {
  status: string;
  service: string;
  registry_frozen: boolean;
  tools_registered: number;
  paired_devices: number;
  pairing_enabled: boolean;
}

export interface Device {
  device_id: string;
  name: string;
  created_at: string;
  revoked: boolean;
  last_seen?: string;
  kind?: string;
}

export interface Session {
  jwt: string;
  deviceId: string;
  name: string;
  createdAt: string;
}

export interface PairingOffer {
  device_id: string;
  pairing_token: string;
  expires_at: number;
}

export interface RemoteSession {
  session_id: string;
  device_id?: string;
  expires_at?: string;
  [key: string]: unknown;
}

export interface CallSession {
  call_id: string;
  status: string;
  source: string;
  turns: number;
  created_at: string;
}

export type CallTriggerKind = "time" | "task_completed";

export interface CallTrigger {
  trigger_id: string;
  kind: CallTriggerKind;
  label: string;
  at: string;
  match: string;
  enabled: boolean;
  one_shot: boolean;
  created_at: string;
  last_fired_at: string | null;
  fired_task_ids: string[];
}

export interface CreateCallTriggerInput {
  kind: CallTriggerKind;
  label?: string;
  at?: string;
  match?: string;
  one_shot?: boolean;
}

const SESSION_KEY = "vioris.session.v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    if (!parsed.jwt || !parsed.deviceId) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveSession(session: Session): void {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_KEY);
}

function authHeaders(): Record<string, string> {
  const session = loadSession();
  return session ? { authorization: `Bearer ${session.jwt}` } : {};
}

function detailFrom(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join(" · ");
    }
    return JSON.stringify(detail);
  }
  return fallback;
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean; headers?: Record<string, string> } = {},
): Promise<T> {
  const { method = "GET", body, auth = true, headers = {} } = options;
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        ...(body !== undefined ? { "content-type": "application/json" } : {}),
        ...(auth ? authHeaders() : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "The local UI host is not reachable.");
  }

  if (response.status === 401 && auth) {
    window.dispatchEvent(new CustomEvent("vioris:unauthorized"));
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;

  if (!response.ok) {
    throw new ApiError(
      response.status,
      detailFrom(payload, response.status === 401 ? "Pairing expired. Pair this browser again." : `HTTP ${response.status}`),
    );
  }
  return payload as T;
}

export async function pairBrowser(name: string, onProgress?: (step: string) => void): Promise<Session> {
  onProgress?.("Creating a one-time pairing token…");
  const offer = await request<PairingOffer>("/auth/device/pair", {
    method: "POST",
    body: { name },
    auth: false,
  });
  onProgress?.("Exchanging it for a device-scoped key…");
  const exchanged = await request<{ jwt: string; token_type: string }>("/auth/device/exchange", {
    method: "POST",
    body: { device_id: offer.device_id, token: offer.pairing_token },
    auth: false,
  });
  const session: Session = {
    jwt: exchanged.jwt,
    deviceId: offer.device_id,
    name,
    createdAt: new Date().toISOString(),
  };
  saveSession(session);
  return session;
}

export async function createPairingOffer(name: string): Promise<PairingOffer> {
  return request<PairingOffer>("/auth/device/pair", { method: "POST", body: { name } });
}

export const api = {
  health: () => request<GatewayHealth>("/health", { auth: false }),

  tasks: async () => (await request<{ tasks: Task[] }>("/v1/tasks")).tasks ?? [],
  task: async (id: string) => (await request<{ task: Task }>(`/v1/tasks/${id}`)).task,
  createTask: (text: string) =>
    request<{
      task: Task | null;
      task_id: string | null;
      blocked_tools?: unknown[];
      killed?: boolean;
      stopped?: string[];
    }>("/v1/tasks", {
      method: "POST",
      body: { request: text, start: true, auto_run: true },
    }),
  runTask: (id: string) =>
    request<{ task: Task }>(`/v1/tasks/${id}/run`, { method: "POST" }),
  pauseTask: (id: string) =>
    request<{ task: Task }>(`/v1/tasks/${id}/pause`, { method: "POST" }),
  resumeTask: (id: string) =>
    request<{ task: Task }>(`/v1/tasks/${id}/resume`, { method: "POST" }),
  cancelTask: (id: string) =>
    request<{ task: Task }>(`/v1/tasks/${id}/cancel`, { method: "POST" }),
  retryStep: (taskId: string, stepId: string) =>
    request<{ task: Task }>(`/v1/tasks/${taskId}/retry/${stepId}`, { method: "POST" }),

  explainTask: async (id: string) =>
    (await request<{ explanation: TaskExplanation }>(`/v1/tasks/${id}/explain`)).explanation,
  explainStep: async (taskId: string, stepId: string) =>
    (await request<{ explanation: StepReasoning & { explanation: string } }>(
      `/v1/tasks/${taskId}/steps/${stepId}/explain`,
    )).explanation,
  askWhy: (query: string) =>
    request<WhyAnswer>("/v1/explain", { method: "POST", body: { query } }),
  recentReasoning: async () =>
    (await request<{ reasoning: StepReasoning[] }>("/v1/explain/recent")).reasoning ?? [],

  approvals: async () =>
    (await request<{ approvals: Approval[] }>("/v1/approvals")).approvals ?? [],
  approve: (id: string, pin?: string) =>
    request<Record<string, unknown>>(`/v1/approvals/${id}/approve`, {
      method: "POST",
      body: pin ? { pin } : {},
    }),
  reject: (id: string) =>
    request<Record<string, unknown>>(`/v1/approvals/${id}/reject`, { method: "POST" }),
  stages: async () =>
    (await request<{ stages: Stage[] }>("/v1/stages")).stages ?? [],
  cancelStage: (id: string) =>
    request<Record<string, unknown>>(`/v1/stages/${id}/cancel`, { method: "POST" }),
  undoStage: (id: string) =>
    request<Record<string, unknown>>(`/v1/stages/${id}/undo`, { method: "POST" }),
  stopEverything: () => request<Record<string, unknown>>("/v1/stop", { method: "POST" }),

  activity: () =>
    request<{ events: AuditEvent[]; chain_ok: boolean }>("/v1/activity"),

  memory: async () => (await request<{ memories: MemoryItem[] }>("/v1/memory")).memories ?? [],
  remember: (body: {
    category: string;
    content: string;
    source: string;
    user_opted_in: boolean;
    confidence?: number;
  }) => request<{ memory: MemoryItem }>("/v1/memory", { method: "POST", body }),
  correctMemory: (id: string, body: { content: string; category?: string }) =>
    request<{ memory: MemoryItem }>(`/v1/memory/correct/${id}`, { method: "POST", body }),
  forget: (id: string) => request<Record<string, unknown>>(`/v1/memory/${id}`, { method: "DELETE" }),

  connections: () => request<ConnectionsResponse>("/v1/connections"),
  systemStatus: () => request<SystemStatus>("/v1/system/status"),

  devices: () => request<Device[]>("/devices"),
  revokeDevice: (id: string) =>
    request<{ revoked: boolean }>(`/devices/${id}/revoke`, { method: "POST" }),
  connectedDevices: () =>
    request<{ phones: number; laptops: number; total: number }>("/v1/devices/connected"),

  remoteSession: async () =>
    (await request<{ session: RemoteSession | null }>("/v1/remote/session")).session,
  startRemote: (name: string, timeoutMinutes: number) =>
    request<{ task: string; note: string }>("/v1/remote/start", {
      method: "POST",
      body: { name, timeout_minutes: timeoutMinutes },
    }),
  endRemote: () => request<Record<string, unknown>>("/v1/remote/end", { method: "POST" }),
  lockRemote: () => request<Record<string, unknown>>("/v1/remote/lock", { method: "POST" }),
  remoteInput: (body: {
    session_id: string;
    action: string;
    text?: string;
    x?: number;
    y?: number;
  }) => request<Record<string, unknown>>("/v1/remote/input", { method: "POST", body }),

  async screenshot(): Promise<string> {
    const response = await fetch("/v1/remote/screenshot", { headers: authHeaders() });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new ApiError(response.status, detailFrom(payload, `HTTP ${response.status}`));
    }
    const blob = await response.blob();
    return URL.createObjectURL(blob);
  },

  calls: async () => (await request<{ calls: CallSession[] }>("/v1/calls")).calls ?? [],
  triggerCall: (demo = true) =>
    request<{ call_id: string | null; status: string; note: string }>("/v1/call/trigger", {
      method: "POST",
      body: { force: true, demo },
    }),
  callTriggers: async () =>
    (await request<{ triggers: CallTrigger[] }>("/v1/call/triggers")).triggers ?? [],
  createCallTrigger: (input: CreateCallTriggerInput) =>
    request<{ trigger: CallTrigger }>("/v1/call/triggers", {
      method: "POST",
      body: input,
    }),
  deleteCallTrigger: (id: string) =>
    request<{ trigger_id: string; deleted: boolean }>(`/v1/call/triggers/${id}`, {
      method: "DELETE",
    }),
  speakCall: (id: string) =>
    request<{ spoken_text?: string; status: string }>(`/v1/call/${id}/speak`, { method: "POST" }),
  callMessage: (id: string, text: string) =>
    request<{ response_text?: string; action?: unknown; digest_status?: string }>(
      `/v1/call/${id}/message`,
      { method: "POST", body: { text } },
    ),
  callStatus: (id: string) =>
    request<{ status: string; turns: number; items_count: number; pending_actions: number }>(
      `/v1/call/${id}/status`,
    ),
};

export function formatError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
