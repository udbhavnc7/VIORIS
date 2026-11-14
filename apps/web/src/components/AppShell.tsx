import { useEffect, useMemo, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { api, formatError, loadSession } from "../lib/api";
import { relativeTime } from "../lib/format";
import { usePrefs } from "../state/prefs";
import { useData } from "../state/store";
import { AmbientField } from "./AmbientField";
import { CommandPalette } from "./CommandPalette";
import { ConfirmDialog } from "./Modal";
import { Icon, type IconName } from "./Icon";
import { LiveDot } from "./Pills";
import { TaskDrawerProvider } from "./TaskDrawer";
import { useToast } from "./Toast";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  badge?: number;
}

const GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Operate",
    items: [
      { to: "/", label: "Overview", icon: "grid" },
      { to: "/workflows", label: "Workflows", icon: "workflow" },
      { to: "/approvals", label: "Approvals", icon: "shield" },
    ],
  },
  {
    label: "Observe",
    items: [
      { to: "/activity", label: "Activity", icon: "activity" },
      { to: "/memory", label: "Memory", icon: "brain" },
    ],
  },
  {
    label: "Connect",
    items: [
      { to: "/connections", label: "Connections", icon: "plug" },
      { to: "/remote", label: "Remote", icon: "monitor" },
      { to: "/devices", label: "Devices", icon: "devices" },
      { to: "/call", label: "Call", icon: "phone" },
    ],
  },
  {
    label: "System",
    items: [{ to: "/settings", label: "Settings", icon: "settings" }],
  },
];

const TITLES: Record<string, { title: string; sub: string }> = {
  "/": { title: "Overview", sub: "Your command center" },
  "/workflows": { title: "Workflows", sub: "Known requests, one step away" },
  "/approvals": { title: "Approvals", sub: "Exact actions, explicit consent" },
  "/activity": { title: "Activity", sub: "Every action, chained and verifiable" },
  "/memory": { title: "Memory", sub: "Remember with permission" },
  "/connections": { title: "Connections", sub: "What is actually wired up" },
  "/remote": { title: "Remote control", sub: "A gated window into this laptop" },
  "/devices": { title: "Devices", sub: "Who can reach this machine" },
  "/settings": { title: "Settings", sub: "Motion, security and runtime" },
  "/call": { title: "Call", sub: "The proactive phone interface" },
  "/pair": { title: "Pair", sub: "Authorize this browser" },
};

export function AppShell({ children, onSignOut }: { children: ReactNode; onSignOut: () => void }) {
  const location = useLocation();
  const { approvals, chainOk, health, connected, lastSync, refreshAll, refreshing } = useData();
  const { prefs } = usePrefs();
  const { push } = useToast();

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [signOutOpen, setSignOutOpen] = useState(false);
  const [stopping, setStopping] = useState(false);

  const session = useMemo(() => loadSession(), []);
  const meta = TITLES[location.pathname] ?? { title: "Vioris", sub: "" };
  const online = Boolean(health?.status === "ok" || health?.status === "healthy");
  const criticalCount = approvals.length;

  const groups = useMemo(
    () =>
      GROUPS.map((group) => ({
        ...group,
        items: group.items.map((item) =>
          item.to === "/approvals" ? { ...item, badge: criticalCount } : item,
        ),
      })),
    [criticalCount],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  const stopEverything = async () => {
    setStopping(true);
    try {
      const result = await api.stopEverything();
      const stopped = (result.result as { stopped?: number } | undefined)?.stopped ?? 0;
      push({
        title: "Everything stopped",
        message: `${stopped} task${stopped === 1 ? "" : "s"} halted. Remote sessions ended.`,
        tone: "warn",
      });
      setStopOpen(false);
      await refreshAll();
    } catch (err) {
      push({ title: "Stop failed", message: formatError(err), tone: "bad" });
    } finally {
      setStopping(false);
    }
  };

  return (
    <div className="app-frame">
      <AmbientField />

      <aside className={`sidebar ${navOpen ? "is-open" : ""}`} aria-label="Primary">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <span className="brand-orbit" />
            <span className="brand-core" />
          </span>
          <span className="brand-copy">
            <strong>VIORIS</strong>
            <em>Personal agent</em>
          </span>
        </div>

        <nav className="nav">
          {groups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-label">{group.label}</span>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) => `nav-item ${isActive ? "is-active" : ""}`}
                >
                  <Icon name={item.icon} size={17} />
                  <span>{item.label}</span>
                  {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-status">
            <LiveDot live={online} />
            <div>
              <strong>{online ? "Gateway online" : "Gateway offline"}</strong>
              <span>{health ? `${health.tools_registered} tools in the frozen registry` : "No health signal yet"}</span>
            </div>
          </div>
          <div className="sidebar-chiprow">
            <span className={`mini-chip ${chainOk === null ? "" : chainOk ? "is-ok" : "is-bad"}`}>
              <Icon name={chainOk === null ? "clock" : chainOk ? "check" : "alert"} size={12} />
              {chainOk === null ? "Chain unchecked" : chainOk ? "Chain verified" : "Chain broken"}
            </span>
            <span className="mini-chip">
              <Icon name="wifi" size={12} />
              Local only
            </span>
          </div>
          <button type="button" className="sidebar-device" onClick={() => setSignOutOpen(true)}>
            <span className="device-avatar">{session?.name?.slice(0, 1).toUpperCase() ?? "V"}</span>
            <span className="device-copy">
              <strong>{session?.name ?? "This browser"}</strong>
              <em>Paired device</em>
            </span>
            <Icon name="logout" size={15} />
          </button>
        </div>
      </aside>

      {navOpen ? (
        <button
          type="button"
          className="nav-scrim"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
        />
      ) : null}

      <div className="main-column">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn nav-toggle"
            aria-label="Open navigation"
            onClick={() => setNavOpen(true)}
          >
            <Icon name="menu" size={19} />
          </button>

          <div className="topbar-title">
            <strong>{meta.title}</strong>
            <span>{meta.sub}</span>
          </div>

          <div className="topbar-actions">
            <button type="button" className="search-trigger" onClick={() => setPaletteOpen(true)}>
              <Icon name="search" size={15} />
              <span>Search or ask</span>
              <kbd>{navigator.platform.toLowerCase().includes("mac") ? "⌘" : "ctrl"} K</kbd>
            </button>

            <span className="topbar-stat" title="Connected device sockets">
              <Icon name="devices" size={14} />
              {connected?.total ?? 0}
            </span>

            <span
              className={`topbar-stat ${chainOk === null ? "" : chainOk ? "is-ok" : "is-bad"}`}
              title="Audit chain integrity"
            >
              <Icon name={chainOk === null ? "clock" : chainOk ? "shield" : "alert"} size={14} />
              {chainOk === null ? "Checking" : chainOk ? "Verified" : "Attention"}
            </span>

            <button
              type="button"
              className="icon-btn"
              aria-label="Refresh"
              title={`Last sync ${lastSync ? relativeTime(new Date(lastSync).toISOString()) : "never"}`}
              onClick={() => void refreshAll()}
            >
              <span className={refreshing ? "spin" : ""}>
                <Icon name="refresh" size={17} />
              </span>
            </button>

            <button type="button" className="btn btn-danger-ghost btn-sm stop-btn" onClick={() => setStopOpen(true)}>
              <Icon name="stop" size={13} />
              <span>Stop everything</span>
            </button>
          </div>
        </header>

        <main className="main-content">{children}</main>

        <footer className="app-footer">
          <span>
            <Icon name="lock" size={12} /> Local-first · nothing leaves this machine by default
          </span>
          <span>Observe broadly. Act narrowly. Never act invisibly.</span>
          <span className="footer-sync">
            {prefs.autoRefresh ? "Live" : "Manual refresh"} · synced {lastSync ? relativeTime(new Date(lastSync).toISOString()) : "—"}
          </span>
        </footer>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

      <ConfirmDialog
        open={stopOpen}
        title="Stop everything?"
        body={
          <div className="confirm-copy">
            <p>
              This halts every non-terminal task immediately and ends any live remote session for
              this device. It does not undo an action that already completed.
            </p>
            <p className="fine-print">The stop itself is written to the audit chain.</p>
          </div>
        }
        confirmLabel="Stop everything"
        busy={stopping}
        onCancel={() => setStopOpen(false)}
        onConfirm={() => void stopEverything()}
      />

      <ConfirmDialog
        open={signOutOpen}
        title="Unpair this browser?"
        body={
          <div className="confirm-copy">
            <p>
              The device key stored in this browser will be discarded. You can pair again from this
              machine at any time. The gateway keeps its own device registry.
            </p>
          </div>
        }
        confirmLabel="Unpair"
        tone="primary"
        onCancel={() => setSignOutOpen(false)}
        onConfirm={onSignOut}
      />
    </div>
  );
}

export function ShellWithDrawer({ children, onSignOut }: { children: ReactNode; onSignOut: () => void }) {
  return (
    <TaskDrawerProvider>
      <AppShell onSignOut={onSignOut}>{children}</AppShell>
    </TaskDrawerProvider>
  );
}
