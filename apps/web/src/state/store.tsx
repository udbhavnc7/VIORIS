import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  api,
  formatError,
  type Approval,
  type AuditEvent,
  type ConnectionsResponse,
  type GatewayHealth,
  type Stage,
  type SystemStatus,
  type Task,
} from "../lib/api";

interface Connected {
  phones: number;
  laptops: number;
  total: number;
}

interface DataState {
  tasks: Task[];
  approvals: Approval[];
  stages: Stage[];
  events: AuditEvent[];
  chainOk: boolean | null;
  health: GatewayHealth | null;
  connected: Connected | null;
  connections: ConnectionsResponse | null;
  system: SystemStatus | null;
  loading: boolean;
  refreshing: boolean;
  lastSync: number | null;
  error: string | null;
  refreshCore: () => Promise<void>;
  refreshSystem: () => Promise<void>;
  refreshAll: () => Promise<void>;
}

const DataContext = createContext<DataState | null>(null);

export function DataProvider({
  children,
  autoRefresh,
}: {
  children: ReactNode;
  autoRefresh: boolean;
}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [stages, setStages] = useState<Stage[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [chainOk, setChainOk] = useState<boolean | null>(null);
  const [health, setHealth] = useState<GatewayHealth | null>(null);
  const [connected, setConnected] = useState<Connected | null>(null);
  const [connections, setConnections] = useState<ConnectionsResponse | null>(null);
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const refreshCore = useCallback(async () => {
    try {
      const [nextTasks, nextApprovals, nextStages, activity] = await Promise.all([
        api.tasks(),
        api.approvals(),
        api.stages(),
        api.activity(),
      ]);
      setTasks(nextTasks);
      setApprovals(nextApprovals);
      setStages(nextStages);
      setEvents(activity.events ?? []);
      setChainOk(activity.chain_ok !== false);
      setLastSync(Date.now());
      setError(null);
    } catch (err) {
      setError(formatError(err));
    }
  }, []);

  const refreshSystem = useCallback(async () => {
    // Update each fact the moment it arrives. A slow connector probe must not
    // make the gateway look offline or hold the audit chain at "unchecked".
    await Promise.all([
      api.health().then(setHealth, () => undefined),
      api.connectedDevices().then(setConnected, () => undefined),
      api.connections().then(setConnections, () => undefined),
      api.systemStatus().then(setSystem, () => undefined),
    ]);
  }, []);

  const refreshAll = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRefreshing(true);
    try {
      await refreshCore();
      setLoading(false);
      await refreshSystem();
    } finally {
      inFlight.current = false;
      setRefreshing(false);
      setLoading(false);
    }
  }, [refreshCore, refreshSystem]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    if (!autoRefresh) return;
    let coreTimer: number | undefined;
    let systemTimer: number | undefined;
    let quick: number | undefined;

    const start = () => {
      coreTimer = window.setInterval(() => void refreshCore(), 5000);
      quick = window.setInterval(() => {
        api.health().then(setHealth).catch(() => undefined);
      }, 15_000);
      systemTimer = window.setInterval(() => void refreshSystem(), 30_000);
    };
    const stop = () => {
      window.clearInterval(coreTimer);
      window.clearInterval(systemTimer);
      window.clearInterval(quick);
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else {
        void refreshCore();
        start();
      }
    };

    start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [autoRefresh, refreshCore, refreshSystem]);

  const value = useMemo<DataState>(
    () => ({
      tasks,
      approvals,
      stages,
      events,
      chainOk,
      health,
      connected,
      connections,
      system,
      loading,
      refreshing,
      lastSync,
      error,
      refreshCore,
      refreshSystem,
      refreshAll,
    }),
    [
      tasks,
      approvals,
      stages,
      events,
      chainOk,
      health,
      connected,
      connections,
      system,
      loading,
      refreshing,
      lastSync,
      error,
      refreshCore,
      refreshSystem,
      refreshAll,
    ],
  );

  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

export function useData(): DataState {
  const context = useContext(DataContext);
  if (!context) throw new Error("useData must be used inside DataProvider");
  return context;
}
