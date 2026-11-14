import { useState } from "react";
import { Link } from "react-router-dom";

import { useData } from "../state/store";
import { useRunCommand } from "../state/useRun";
import { Composer } from "../components/Composer";
import { PageHeader } from "../components/EmptyState";
import { Icon, type IconName } from "../components/Icon";
import { RiskBadge } from "../components/Pills";
import { TaskRow } from "../components/TaskRow";
import type { RiskTier } from "../lib/api";

interface Workflow {
  id: string;
  title: string;
  blurb: string;
  command: string;
  icon: IconName;
  tiers: RiskTier[];
  note?: string;
}

const GROUPS: Array<{ label: string; caption: string; items: Workflow[] }> = [
  {
    label: "Morning",
    caption: "Read-only sweeps. They run the moment you ask.",
    items: [
      {
        id: "brief",
        title: "Daily briefing",
        blurb: "Unread mail, the next 24 hours, and your WhatsApp digest in one pass.",
        command: "Brief me",
        icon: "spark",
        tiers: ["observe", "observe", "observe"],
      },
      {
        id: "time",
        title: "Read the clock",
        blurb: "The local time and date, straight from this machine.",
        command: "What time is it",
        icon: "clock",
        tiers: ["observe"],
      },
      {
        id: "calendar",
        title: "Next 24 hours",
        blurb: "List upcoming events without touching anything.",
        command: "What's on my calendar",
        icon: "calendar",
        tiers: ["observe"],
      },
    ],
  },
  {
    label: "Messages",
    caption: "One is read-only. One stops for your approval before anything leaves.",
    items: [
      {
        id: "mail",
        title: "Unread mail",
        blurb: "Fetch a short digest of unread messages from the connected inbox.",
        command: "Check unread mail",
        icon: "mail",
        tiers: ["observe"],
      },
      {
        id: "whatsapp",
        title: "WhatsApp digest",
        blurb: "Summarize recent messages. Sending is a separate, gated action.",
        command: "Any new WhatsApp messages",
        icon: "message",
        tiers: ["observe"],
      },
      {
        id: "send",
        title: "Send a message",
        blurb: "Plan an exact message, then hold at the approval gate with the full text visible.",
        command: "Send a message to Disha saying I'm running late",
        icon: "send",
        tiers: ["execute"],
        note: "Demo transports refuse to deliver — honestly.",
      },
    ],
  },
  {
    label: "People & plans",
    caption: "Local reads and staged changes. Nothing external fires silently.",
    items: [
      {
        id: "contacts",
        title: "Find a person",
        blurb: "Search contacts by name or alias.",
        command: "Find Disha in contacts",
        icon: "contact",
        tiers: ["observe"],
      },
      {
        id: "reminder",
        title: "Set a reminder",
        blurb: "Write a local reminder entry. Prepare-tier: staged, not sent anywhere.",
        command: "Remind me to call mom at 3pm",
        icon: "clock",
        tiers: ["prepare"],
      },
      {
        id: "reserve",
        title: "Look for a table",
        blurb: "Read availability for a venue. Booking separately needs approval.",
        command: "Reserve a table at Luna for 4",
        icon: "calendar",
        tiers: ["observe"],
        note: "Demo availability is sample data.",
      },
    ],
  },
];

export function Workflows() {
  const { tasks, connections, system } = useData();
  const { run } = useRunCommand();
  const [runningId, setRunningId] = useState<string | null>(null);

  const start = async (workflow: Workflow) => {
    setRunningId(workflow.id);
    await run(workflow.command);
    setRunningId(null);
  };

  const demo = connections?.demo_mode;

  return (
    <div className="page workflows-page">
      <PageHeader
        eyebrow="Request library"
        title="Known requests, one step away"
        intro="Each card is a deterministic workflow the local planner can build without a model. Observation runs immediately; anything that changes the world pauses for your approval."
        actions={
          <Link to="/approvals" className="btn btn-ghost">
            <Icon name="shield" size={16} /> Approvals
          </Link>
        }
      />

      <div className="workflow-layout">
        <div className="workflow-main">
          <Composer variant="panel" placeholder="Or describe something new…" />

          {demo ? (
            <div className="callout callout-info">
              <Icon name="plug" size={16} />
              <div>
                <strong>Sample connectors are on.</strong>
                <span>
                  Reads show clearly-labelled demo data. A send will fail honestly instead of
                  claiming it was delivered.
                </span>
              </div>
            </div>
          ) : null}

          {GROUPS.map((group) => (
            <section className="workflow-group" key={group.label}>
              <div className="workflow-group-head">
                <h2>{group.label}</h2>
                <p>{group.caption}</p>
              </div>
              <div className="workflow-cards">
                {group.items.map((workflow) => (
                  <article className="workflow-card" key={workflow.id}>
                    <div className="workflow-icon">
                      <Icon name={workflow.icon} size={19} />
                    </div>
                    <div className="workflow-copy">
                      <h3>{workflow.title}</h3>
                      <p>{workflow.blurb}</p>
                      <div className="workflow-meta">
                        {workflow.tiers.map((tier, index) => (
                          <RiskBadge key={`${workflow.id}-${index}`} tier={tier} />
                        ))}
                        <code className="workflow-command">{workflow.command}</code>
                      </div>
                      {workflow.note ? <p className="workflow-note">{workflow.note}</p> : null}
                    </div>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm workflow-run"
                      onClick={() => void start(workflow)}
                      disabled={runningId !== null}
                    >
                      {runningId === workflow.id ? (
                        <span className="spinner" aria-hidden="true" />
                      ) : (
                        <Icon name="play" size={13} />
                      )}
                      {runningId === workflow.id ? "Planning" : "Run"}
                    </button>
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>

        <aside className="workflow-rail">
          <section className="panel">
            <div className="section-head">
              <div>
                <h2>Recent runs</h2>
                <p>Tap any task for the exact step record.</p>
              </div>
            </div>
            {tasks.length === 0 ? (
              <p className="rail-empty">No runs yet. Pick a card and Vioris will plan it.</p>
            ) : (
              <div className="task-list">
                {tasks.slice(0, 8).map((task) => (
                  <TaskRow key={task.task_id} task={task} showRisk={false} />
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="section-head">
              <div>
                <h2>How a request moves</h2>
                <p>The loop never skips a gate.</p>
              </div>
            </div>
            <ol className="loop-list">
              <li>
                <span>01</span>
                <div>
                  <strong>Understand</strong>
                  <p>Your words become a plan. No tool runs while planning.</p>
                </div>
              </li>
              <li>
                <span>02</span>
                <div>
                  <strong>Classify</strong>
                  <p>
                    The frozen registry assigns Observe, Prepare, Execute, or Critical. The model
                    never chooses risk.
                  </p>
                </div>
              </li>
              <li>
                <span>03</span>
                <div>
                  <strong>Approve</strong>
                  <p>Execute and Critical stop with an exact diff. Critical adds PIN and cooldown.</p>
                </div>
              </li>
              <li>
                <span>04</span>
                <div>
                  <strong>Verify & record</strong>
                  <p>The result is checked independently, then written to the hash chain.</p>
                </div>
              </li>
            </ol>
            <div className="runtime-note">
              <Icon name="lock" size={14} />
              {system?.tools_registered
                ? `${system.tools_registered} tools are registered. Anything else is refused, not guessed.`
                : "Waiting for the frozen registry."}
            </div>
          </section>

          <section className="panel panel-quiet">
            <h2>Need freeform planning?</h2>
            <p>
              Known workflows work offline. A request outside them needs a local model — Vioris
              will say so plainly rather than invent a plan.
            </p>
            <Link to="/connections" className="text-link">
              Check local planner status <Icon name="chevronRight" size={13} />
            </Link>
          </section>
        </aside>
      </div>
    </div>
  );
}
