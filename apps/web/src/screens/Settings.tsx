import { useState } from "react";

import { formatError, loadSession } from "../lib/api";
import { Icon, type IconName } from "../components/Icon";
import { ConfirmDialog } from "../components/Modal";
import { PageHeader, SectionHead } from "../components/EmptyState";
import { useToast } from "../components/Toast";
import { usePrefs, type Prefs } from "../state/prefs";
import { useData } from "../state/store";
import { usePageTitle } from "../hooks/useNow";

const SERVICE_ROWS: Array<{ label: string; url: string; key: string }> = [
  { label: "API gateway", url: "127.0.0.1:8420", key: "gateway" },
  { label: "Task runner", url: "127.0.0.1:8421", key: "task runner" },
  { label: "Memory service", url: "127.0.0.1:8441", key: "memory" },
  { label: "Computer agent", url: "127.0.0.1:8430", key: "computer agent" },
  { label: "Demo connectors", url: "127.0.0.1:8460", key: "connectors" },
  { label: "This UI host", url: "127.0.0.1:8425", key: "ui" },
];

function Switch({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  description: string;
}) {
  return (
    <label className="setting-row">
      <span className="setting-copy">
        <strong>{label}</strong>
        <em>{description}</em>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        className={`switch ${checked ? "is-on" : ""}`}
        onClick={() => onChange(!checked)}
      >
        <span className="switch-thumb" />
      </button>
    </label>
  );
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (next: T) => void;
  label: string;
}) {
  return (
    <div className="setting-row">
      <span className="setting-copy">
        <strong>{label}</strong>
        <em>Applies immediately and is remembered in this browser.</em>
      </span>
      <div className="segmented" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === value ? "is-active" : ""}
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Fact({
  icon,
  label,
  value,
  tone = "muted",
}: {
  icon: IconName;
  label: string;
  value: string;
  tone?: "ok" | "warn" | "muted" | "violet";
}) {
  return (
    <div className={`fact fact-${tone}`}>
      <span className="fact-icon">
        <Icon name={icon} size={15} />
      </span>
      <div>
        <span className="fact-label">{label}</span>
        <strong className="fact-value">{value}</strong>
      </div>
    </div>
  );
}

export function Settings({ onSignOut }: { onSignOut: () => void }) {
  usePageTitle("Settings");
  const { prefs, update, reducedMotion } = usePrefs();
  const { system, health, chainOk } = useData();
  const { push } = useToast();
  const [signOutOpen, setSignOutOpen] = useState(false);
  const session = loadSession();

  const criticalLocked = system?.critical_actions !== "unlocked";
  const demoMode = Boolean(system?.demo_mode ?? health?.pairing_enabled === false);

  const updatePref = <K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    update({ [key]: value } as Partial<Prefs>);
    push({ title: "Preference saved", message: "Stored locally in this browser.", tone: "ok" });
  };

  return (
    <div className="screen settings-screen">
      <PageHeader
        eyebrow="System"
        title="Settings"
        intro="Local preferences, the security posture that is actually in force, and the services this interface is allowed to reach."
        actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSignOutOpen(true)}>
            <Icon name="logout" size={14} />
            <span>Unpair this browser</span>
          </button>
        }
      />

      <div className="settings-grid">
        <section className="panel">
          <SectionHead
            title="Interface"
            note="Nothing here changes backend behavior. These are display choices stored in this browser."
          />
          <div className="setting-list">
            <Switch
              label="Live data refresh"
              description="Poll tasks, approvals and the audit chain every five seconds while this tab is visible."
              checked={prefs.autoRefresh}
              onChange={(next) => updatePref("autoRefresh", next)}
            />
            <Segmented
              label="Motion"
              value={prefs.reduceMotion}
              options={[
                { value: "system", label: "System" },
                { value: "off", label: "Full" },
                { value: "on", label: "Reduce" },
              ]}
              onChange={(next) => updatePref("reduceMotion", next)}
            />
            <Switch
              label="Show identifiers"
              description="Display full task, approval and hash identifiers instead of short prefixes."
              checked={prefs.showIds}
              onChange={(next) => updatePref("showIds", next)}
            />
          </div>
          <p className="fine-print">
            {reducedMotion
              ? "Motion is reduced right now — the orb and ambient field render a single static frame."
              : "The orb and ambient field animate right now. Reduce Motion also follows your operating system."}
          </p>
        </section>

        <section className="panel">
          <SectionHead
            title="Security posture"
            note="Read directly from the gateway. The interface cannot relax any of it."
          />
          <div className="fact-grid">
            <Fact
              icon={criticalLocked ? "lock" : "lockOpen"}
              label="Critical actions"
              value={criticalLocked ? "Locked" : "Unlocked"}
              tone={criticalLocked ? "ok" : "warn"}
            />
            <Fact
              icon="shield"
              label="Audit chain"
              value={chainOk === null ? "Checking" : chainOk ? "Verified" : "Broken"}
              tone={chainOk === null ? "violet" : chainOk ? "ok" : "warn"}
            />
            <Fact
              icon="terminal"
              label="Tool registry"
              value={
                health
                  ? `${health.tools_registered} tools · ${health.registry_frozen ? "frozen" : "mutable"}`
                  : "No signal"
              }
              tone="violet"
            />
            <Fact
              icon="wifi"
              label="Network"
              value={system?.local_only === false ? "Reachable remotely" : "Local only"}
              tone={system?.local_only === false ? "warn" : "ok"}
            />
          </div>
          <p className="fine-print">
            {system?.critical_note ??
              "Critical actions need an approval, a local PIN and a cooldown. The PIN is supplied with VIORIS_CRITICAL_PIN when the gateway and task runner start; it never reaches the model or this interface."}
          </p>
        </section>

        <section className="panel">
          <SectionHead
            title="Runtime"
            note="Loopback endpoints. Only the gateway is reachable through this UI host."
          />
          <div className="service-list">
            {SERVICE_ROWS.map((row) => {
              let state: "ok" | "warn" | "muted" = "muted";
              let stateLabel = "Unknown";
              if (row.key === "gateway") {
                state = health?.status === "ok" || health?.status === "healthy" ? "ok" : "warn";
                stateLabel = state === "ok" ? "Online" : "Offline";
              } else {
                const probe =
                  row.key === "task runner"
                    ? system?.task_runner_state
                    : row.key === "memory"
                      ? system?.memory_state
                      : row.key === "computer agent"
                        ? system?.computer_agent_state
                        : undefined;
                if (probe) {
                  state = probe.reachable ? "ok" : "warn";
                  stateLabel = probe.reachable ? "Reachable" : "No answer";
                } else if (row.key === "ui") {
                  state = "ok";
                  stateLabel = "Serving this page";
                } else if (row.key === "connectors") {
                  state = demoMode ? "ok" : "muted";
                  stateLabel = demoMode ? "Demo data on" : "Optional";
                }
              }
              return (
                <div className="service-row" key={row.key}>
                  <span className={`service-dot tone-${state}`} aria-hidden="true" />
                  <span className="service-label">{row.label}</span>
                  <code>{row.url}</code>
                  <span className={`service-state tone-${state}`}>{stateLabel}</span>
                </div>
              );
            })}
          </div>
          <p className="fine-print">
            The browser never talks to the task runner, memory or connectors directly — every request
            is proxied through the UI host to the authenticated gateway.
          </p>
        </section>

        <section className="panel">
          <SectionHead
            title="This browser"
            note="A device-scoped key, kept only in this browser's storage."
          />
          <div className="session-card">
            <span className="device-avatar">{session?.name?.slice(0, 1).toUpperCase() ?? "V"}</span>
            <div className="session-copy">
              <strong>{session?.name ?? "This browser"}</strong>
              <em>
                Paired {session ? new Date(session.createdAt).toLocaleString() : "—"}
              </em>
              <code>
                {prefs.showIds
                  ? session?.deviceId ?? "—"
                  : session
                    ? `${session.deviceId.slice(0, 12)}…`
                    : "—"}
              </code>
            </div>
          </div>
          <ul className="plain-list">
            <li>The key is scoped to this one device and can be revoked from Devices.</li>
            <li>Unpairing discards the key here; the gateway keeps its own registry.</li>
            <li>Phone access should travel over Tailscale, never a public port.</li>
          </ul>
          <div className="button-row">
            <button type="button" className="btn btn-ghost" onClick={() => setSignOutOpen(true)}>
              <Icon name="logout" size={14} />
              <span>Unpair</span>
            </button>
          </div>
        </section>

        {demoMode ? (
          <section className="panel panel-accent">
            <SectionHead
              title="Demo mode is on"
              note="Sample connectors answer reads. Writes are refused rather than faked."
            />
            <p className="lede">
              Gmail, WhatsApp, calendar and smart home return deterministic sample data so the full
              loop can be exercised offline. Nothing is sent, booked or switched. When real
              credentials exist, the same tools switch to live providers.
            </p>
            <p className="fine-print">
              External writes stay blocked until a provider is configured. Approval of a demo write
              still records the attempt in the audit chain.
            </p>
          </section>
        ) : null}

        <section className="panel">
          <SectionHead title="What this build deliberately does not do" />
          <ul className="plain-list">
            <li>No server-initiated phone calls — the phone dials out, never the laptop.</li>
            <li>No public inbound ports; the loopback host is the only listener.</li>
            <li>No credential storage in the browser or in the model context.</li>
            <li>No silent Execute or Critical actions — an approval is always required.</li>
            <li>No risk assignment by the model; the frozen registry is the only authority.</li>
          </ul>
          <p className="fine-print">
            Errors surface as errors. The interface will not invent a “sent”, “connected” or
            “completed” state that the backend did not report.
          </p>
        </section>
      </div>

      <ConfirmDialog
        open={signOutOpen}
        title="Unpair this browser?"
        body={
          <div className="confirm-copy">
            <p>
              The device key in this browser will be discarded. The gateway keeps its own device
              registry, so you can pair again from this machine — or revoke it from Devices.
            </p>
            <p className="fine-print">This does not stop running tasks.</p>
          </div>
        }
        confirmLabel="Unpair"
        tone="primary"
        onCancel={() => setSignOutOpen(false)}
        onConfirm={() => {
          setSignOutOpen(false);
          try {
            onSignOut();
          } catch (err) {
            push({ title: "Could not unpair", message: formatError(err), tone: "bad" });
          }
        }}
      />
    </div>
  );
}
