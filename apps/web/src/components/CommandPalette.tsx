import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useRunCommand } from "../state/useRun";
import { Icon, type IconName } from "./Icon";
import { Modal } from "./Modal";

const NAV: Array<{ label: string; hint: string; path: string; icon: IconName }> = [
  { label: "Overview", hint: "Command center", path: "/", icon: "grid" },
  { label: "Workflows", hint: "Run a known request", path: "/workflows", icon: "workflow" },
  { label: "Approvals", hint: "Review exact actions", path: "/approvals", icon: "shield" },
  { label: "Activity", hint: "Hash-chained audit", path: "/activity", icon: "activity" },
  { label: "Memory", hint: "What Vioris remembers", path: "/memory", icon: "brain" },
  { label: "Connections", hint: "Honest integration status", path: "/connections", icon: "plug" },
  { label: "Remote control", hint: "Screen and input", path: "/remote", icon: "monitor" },
  { label: "Devices", hint: "Paired browsers and phones", path: "/devices", icon: "devices" },
  { label: "Call", hint: "Proactive call interface", path: "/call", icon: "phone" },
  { label: "Settings", hint: "Motion, security, runtime", path: "/settings", icon: "settings" },
];

const COMMANDS = [
  "Brief me",
  "Check unread mail",
  "What's on my calendar",
  "Any new WhatsApp messages",
  "What time is it",
  "Send a message to Disha saying I'm running late",
];

type Item =
  | { kind: "nav"; id: string; label: string; hint: string; icon: IconName; path: string }
  | { kind: "run"; id: string; label: string; hint: string; text: string };

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const navigate = useNavigate();
  const { run, busy } = useRunCommand();
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(0);
    }
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const q = query.trim().toLowerCase();
    const nav = NAV.filter(
      (item) => !q || item.label.toLowerCase().includes(q) || item.hint.toLowerCase().includes(q),
    ).map<Item>((item) => ({ kind: "nav", id: `nav:${item.path}`, ...item }));

    const commands = COMMANDS.filter((command) => !q || command.toLowerCase().includes(q)).map<Item>(
      (command) => ({
        kind: "run",
        id: `run:${command}`,
        label: command,
        hint: "Run as a task",
        text: command,
      }),
    );

    const trimmed = query.trim();
    const exact = COMMANDS.some((command) => command.toLowerCase() === trimmed.toLowerCase());
    if (trimmed.length > 2 && !exact) {
      commands.unshift({
        kind: "run",
        id: `run:custom`,
        label: `Ask: “${trimmed}”`,
        hint: "Plan it with the tools I have",
        text: trimmed,
      });
    }
    return [...nav, ...commands];
  }, [query]);

  useEffect(() => {
    setSelected((current) => Math.min(current, Math.max(0, items.length - 1)));
  }, [items.length]);

  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>(".palette-item.is-selected");
    node?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const choose = async (item: Item | undefined) => {
    if (!item || busy) return;
    if (item.kind === "nav") {
      navigate(item.path);
      onClose();
      return;
    }
    onClose();
    await run(item.text);
  };

  return (
    <Modal open={open} onClose={onClose} title="Command" eyebrow="Jump or ask" size="md">
      <div className="palette">
        <div className="palette-search">
          <Icon name="search" size={17} />
          <input
            value={query}
            placeholder="Search screens, or type a request…"
            aria-label="Search commands"
            onChange={(event) => {
              setQuery(event.target.value);
              setSelected(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setSelected((current) => Math.min(current + 1, items.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setSelected((current) => Math.max(current - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                void choose(items[selected]);
              }
            }}
          />
          <kbd>esc</kbd>
        </div>

        <div className="palette-list" ref={listRef} role="listbox" aria-label="Commands">
          {items.length === 0 ? <p className="palette-empty">Nothing matches that yet.</p> : null}
          {items.map((item, index) => (
            <button
              key={item.id}
              type="button"
              role="option"
              aria-selected={index === selected}
              className={`palette-item ${index === selected ? "is-selected" : ""}`}
              onMouseEnter={() => setSelected(index)}
              onClick={() => void choose(item)}
            >
              <span className="palette-icon">
                <Icon name={item.kind === "nav" ? item.icon : "spark"} size={16} />
              </span>
              <span className="palette-copy">
                <strong>{item.label}</strong>
                <em>{item.hint}</em>
              </span>
              <span className="palette-kind">{item.kind === "nav" ? "Go" : "Run"}</span>
            </button>
          ))}
        </div>

        <div className="palette-foot">
          <span>
            <kbd>↑</kbd> <kbd>↓</kbd> move
          </span>
          <span>
            <kbd>↵</kbd> select
          </span>
          <span className="palette-safety">
            <Icon name="shield" size={12} /> Risky steps still need approval
          </span>
        </div>
      </div>
    </Modal>
  );
}
