import { useMemo, useState } from "react";

import { useData } from "../state/store";
import { PageHeader } from "../components/EmptyState";
import { Icon, type IconName } from "../components/Icon";
import { ModePill } from "../components/Pills";
import type { Connection } from "../lib/api";

const ICONS: Record<string, IconName> = {
  gmail: "mail",
  calendar: "calendar",
  whatsapp: "message",
  contacts: "contact",
  notes: "edit",
  bookmarks: "copy",
  history: "clock",
  cloud_files: "copy",
  telephony: "phone",
  reservations: "calendar",
  smart_home: "sun",
  computer: "monitor",
  memory: "brain",
  ollama: "spark",
};

const CATEGORY_LABEL: Record<Connection["category"], string> = {
  communication: "Communication",
  knowledge: "Knowledge",
  actions: "Actions",
  local: "Local services",
};

const CATEGORY_NOTE: Record<Connection["category"], string> = {
  communication: "Reads and sends. Sends always stop at the approval gate.",
  knowledge: "Local indexes and saved context. Read-mostly.",
  actions: "Controls and sessions. Execute-tier at minimum.",
  local: "Runs on this machine. No account, no cloud, no bill.",
};

export function Connections() {
  const { connections, system, health, refreshSystem, refreshing } = useData();
  const [filter, setFilter] = useState<Connection["category"] | "all">("all");

  const grouped = useMemo(() => {
    const list = connections?.connections ?? [];
    const categories = (["communication", "knowledge", "actions", "local"] as const).filter(
      (category) => filter === "all" || filter === category,
    );
    return categories.map((category) => ({
      category,
      items: list.filter((item) => item.category === category),
    }));
  }, [connections, filter]);

  const configured = connections?.configured_count ?? 0;
  const total = connections?.total ?? 0;
  const demo = connections?.demo_mode;
  const probing = connections === null;

  return (
    <div className="page connections-page">
      <PageHeader
        eyebrow="Integration truth"
        title="What is actually wired up"
        intro="These statuses are probed live. A connector is only “live” when it answers; demo payloads are labelled as samples; an unreachable connector says so instead of pretending."
        actions={
          <div className="page-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void refreshSystem()}>
              <span className={refreshing ? "spin" : ""}>
                <Icon name="refresh" size={14} />
              </span>
              Re-probe
            </button>
            <span className={`page-chip ${probing ? "" : demo ? "is-info" : "is-ok"}`}>
              <Icon name={probing ? "clock" : demo ? "plug" : "shield"} size={14} />
              {probing ? "Probing" : demo ? "Demo connectors" : "Live posture"}
            </span>
          </div>
        }
      />

      <div className="connection-hero">
        <div className="connection-score">
          <strong>
            {probing ? "—" : configured}
            <em>/{probing ? "—" : total || "—"}</em>
          </strong>
          <span>services reachable</span>
        </div>
        <div className="connection-bars" aria-hidden="true">
          {(connections?.connections ?? []).map((item) => (
            <span
              key={item.id}
              className={`connection-bar mode-${item.mode}`}
              title={`${item.name}: ${item.detail}`}
            />
          ))}
        </div>
        <div className="connection-hero-copy">
          <strong>
            {probing
              ? "Probing every connector…"
              : demo
                ? "Sample data is switched on."
                : "No sample data is being served."}
          </strong>
          <p>
            {probing
              ? "The gateway asks each service whether it answers. Nothing is assumed while the probe runs."
              : demo
                ? "Reads return clearly-labelled demo payloads so every observation workflow can finish. Writes refuse with a reason — an approved send fails honestly."
                : "Every read comes from a connector that answered its health probe. Nothing is mocked silently."}
          </p>
        </div>
        <div className="connection-hero-meta">
          <span>
            <Icon name="wifi" size={14} /> {health?.service ?? "gateway"}
          </span>
          <span>
            <Icon name="lock" size={14} /> {system?.local_only ? "loopback only" : "check binding"}
          </span>
        </div>
      </div>

      <div className="segmented connections-filter" role="tablist" aria-label="Connection category">
        <button
          type="button"
          role="tab"
          aria-selected={filter === "all"}
          className={filter === "all" ? "is-active" : ""}
          onClick={() => setFilter("all")}
        >
          All <em>{connections?.connections.length ?? 0}</em>
        </button>
        {(Object.keys(CATEGORY_LABEL) as Array<Connection["category"]>).map((category) => (
          <button
            key={category}
            type="button"
            role="tab"
            aria-selected={filter === category}
            className={filter === category ? "is-active" : ""}
            onClick={() => setFilter(category)}
          >
            {CATEGORY_LABEL[category]}
          </button>
        ))}
      </div>

      {probing ? (
        <div className="panel panel-quiet connection-loading">
          <span className="spinner" aria-hidden="true" />
          <div>
            <strong>Probing every connector</strong>
            <span>
              Health checks run in parallel. A service appears as live only once it answers.
            </span>
          </div>
        </div>
      ) : (
        <div className="connection-groups">
          {grouped.map((group) =>
            group.items.length === 0 ? null : (
              <section className="connection-group" key={group.category}>
                <div className="connection-group-head">
                  <h2>{CATEGORY_LABEL[group.category]}</h2>
                  <p>{CATEGORY_NOTE[group.category]}</p>
                </div>
                <div className="connection-grid">
                  {group.items.map((item) => (
                    <article className={`connection-card connection-${item.mode}`} key={item.id}>
                      <div className="connection-top">
                        <span className="connection-icon">
                          <Icon name={ICONS[item.id] ?? "plug"} size={18} />
                        </span>
                        <div>
                          <h3>{item.name}</h3>
                          <span className={`connection-detail ${item.configured ? "is-on" : "is-off"}`}>
                            {item.detail}
                          </span>
                        </div>
                        <ModePill mode={item.mode} />
                      </div>
                      <div className="connection-caps">
                        {item.capabilities.map((capability) => (
                          <span key={capability}>{capability}</span>
                        ))}
                      </div>
                      {item.note ? <p className={`connection-note ${item.mode === "demo" ? "is-demo" : ""}`}>{item.note}</p> : null}
                    </article>
                  ))}
                </div>
              </section>
            ),
          )}
        </div>
      )}

      <div className="connections-foot">
        <section className="panel">
          <div className="section-head">
            <div>
              <h2>How this page stays honest</h2>
              <p>No connector is marked “connected” from configuration alone.</p>
            </div>
          </div>
          <ul className="plain-list">
            <li>The gateway probes each service’s health endpoint and reports what answered.</li>
            <li>Demo payloads carry a notice and are labelled “Sample data” everywhere they surface.</li>
            <li>Sends and bookings are never simulated; a missing transport fails with a reason.</li>
            <li>Secrets live in the local environment, not in this UI and never in the model context.</li>
          </ul>
        </section>
        <section className="panel panel-quiet">
          <h2>Adding a real connection</h2>
          <p>
            Point the matching environment variable at a local connector and restart the gateway.
            Tokens stay on this machine; OAuth grants should use least privilege. There is no cloud
            relay and no paid API in the default path.
          </p>
          <div className="env-list">
            <code>VIORUS_GMAIL_URL</code>
            <code>VIORUS_WHATSAPP_URL</code>
            <code>VIORUS_CALENDAR_URL</code>
            <code>VIORUS_CONTACTS_URL</code>
            <code>VIORUS_COMPUTER_URL</code>
            <code>VIORIS_MEMORY_URL</code>
          </div>
        </section>
      </div>
    </div>
  );
}
