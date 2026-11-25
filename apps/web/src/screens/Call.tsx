import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  formatError,
  type CallSession,
  type CallTrigger,
  type CallTriggerKind,
} from "../lib/api";
import { clockTime, relativeTime, titleCase } from "../lib/format";
import { useData } from "../state/store";
import { PageHeader } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { useToast } from "../components/Toast";

interface Turn {
  id: number;
  role: "vioris" | "you" | "system";
  text: string;
  meta?: string;
}

const STATUS_TONE: Record<string, string> = {
  ringing: "warn",
  active: "info",
  spoken: "info",
  completed: "ok",
  failed: "bad",
};

function actionSummary(action: unknown): string | null {
  if (!action || typeof action !== "object") return null;
  const record = action as Record<string, unknown>;
  const type = typeof record.type === "string" ? record.type : "action";
  const status = typeof record.status === "string" ? record.status : null;
  const recipient = typeof record.recipient === "string" ? ` → ${record.recipient}` : "";
  const label =
    type === "not_sent"
      ? "Prepared, not sent"
      : type === "not_executed"
        ? "Prepared, not executed"
        : type === "send_mail"
          ? "Mail prepared"
          : titleCase(type);
  return `${label}${recipient}${status ? ` · ${status.replace(/_/g, " ")}` : ""}`;
}

export function Call() {
  const { connections, system } = useData();
  const { push } = useToast();

  const [calls, setCalls] = useState<CallSession[]>([]);
  const [triggers, setTriggers] = useState<CallTrigger[]>([]);
  const [triggerKind, setTriggerKind] = useState<CallTriggerKind>("time");
  const [triggerAt, setTriggerAt] = useState("19:00");
  const [triggerMatch, setTriggerMatch] = useState("");
  const [triggerLabel, setTriggerLabel] = useState("");
  const [triggerOneShot, setTriggerOneShot] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("none");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const counter = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadCalls = useCallback(async () => {
    try {
      const list = await api.calls();
      setCalls(list);
    } catch {
      setCalls([]);
    }
  }, []);

  useEffect(() => {
    void loadCalls();
    const timer = window.setInterval(() => void loadCalls(), 10_000);
    return () => window.clearInterval(timer);
  }, [loadCalls]);

  const loadTriggers = useCallback(async () => {
    try {
      setTriggers(await api.callTriggers());
    } catch {
      setTriggers([]);
    }
  }, []);

  useEffect(() => {
    void loadTriggers();
  }, [loadTriggers]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  const add = (role: Turn["role"], message: string, meta?: string) => {
    counter.current += 1;
    setTurns((current) => [...current, { id: counter.current, role, text: message, meta }]);
  };

  const active = useMemo(() => calls.find((call) => call.call_id === activeId) ?? null, [calls, activeId]);

  const trigger = async () => {
    setBusy("trigger");
    try {
      const result = await api.triggerCall();
      if (!result.call_id) {
        push({ title: "The schedule did not fire", message: result.note, tone: "info" });
        return;
      }
      setActiveId(result.call_id);
      setStatus(result.status);
      setTurns([]);
      add(
        "system",
        "Call created. This text console is attached to the same session the phone would answer.",
        result.note,
      );
      await loadCalls();
    } catch (err) {
      push({ title: "Could not start a call", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const addTrigger = async () => {
    setBusy("add-trigger");
    try {
      await api.createCallTrigger({
        kind: triggerKind,
        label: triggerLabel,
        at: triggerKind === "time" ? triggerAt : undefined,
        match: triggerKind === "task_completed" ? triggerMatch : undefined,
        one_shot: triggerOneShot,
      });
      setTriggerLabel("");
      setTriggerMatch("");
      setTriggerOneShot(false);
      await loadTriggers();
      push({ title: "Trigger added", message: "Vioris will call when it next checks.", tone: "ok" });
    } catch (err) {
      push({ title: "Could not add trigger", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const removeTrigger = async (trigger: CallTrigger) => {
    setBusy(`delete-${trigger.trigger_id}`);
    try {
      await api.deleteCallTrigger(trigger.trigger_id);
      await loadTriggers();
    } catch (err) {
      push({ title: "Could not delete trigger", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const speak = async () => {
    if (!activeId) return;
    setBusy("speak");
    try {
      const result = await api.speakCall(activeId);
      if (result.spoken_text) {
        add("vioris", result.spoken_text, "opening digest");
        setStatus(result.status);
      }
    } catch (err) {
      push({ title: "Could not speak the digest", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const send = async (utterance: string) => {
    const message = utterance.trim();
    if (!activeId || !message) return;
    add("you", message);
    setText("");
    setBusy("send");
    try {
      const result = await api.callMessage(activeId, message);
      if (result.response_text) {
        add("vioris", result.response_text, result.action ? actionSummary(result.action) ?? undefined : undefined);
      }
      if (result.digest_status) setStatus(result.digest_status);
      await loadCalls();
    } catch (err) {
      push({ title: "Vioris did not answer", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  const focus = async (call: CallSession) => {
    setActiveId(call.call_id);
    setTurns([]);
    setStatus(call.status);
    add("system", `Attached to call ${call.call_id}. Only turns from this console are shown.`, `${call.source} · ${call.turns} recorded turns`);
    try {
      const current = await api.callStatus(call.call_id);
      setStatus(current.status);
    } catch {
      /* the list already carried a status */
    }
  };

  const demo = connections?.demo_mode;

  return (
    <div className="page call-page">
      <PageHeader
        eyebrow="Proactive voice"
        title="The phone interface, exercised"
        intro="This console drives the same call session the phone answers. Opening lines and replies come from the pipeline; audio synthesis belongs to the phone, not this page."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => void trigger()} disabled={busy !== null}>
            {busy === "trigger" ? <span className="spinner" aria-hidden="true" /> : <Icon name="phone" size={15} />}
            Trigger a demo call
          </button>
        }
      />

      {demo ? (
        <div className="callout callout-info">
          <Icon name="spark" size={16} />
          <div>
            <strong>Sample connectors feed this call.</strong>
            <span>Digest items are demo data. Spoken replies are real pipeline output.</span>
          </div>
        </div>
      ) : null}

      <div className="call-layout">
        <section className={`call-console ${active ? "is-live" : ""}`}>
          <div className="call-console-head">
            <div className="call-peer">
              <span className="call-avatar">
                <span className="call-avatar-ring" />
                <Icon name="spark" size={20} />
              </span>
              <div>
                <strong>Vioris</strong>
                <span>{active ? `call ${active.call_id}` : "No call attached"}</span>
              </div>
            </div>
            <span className={`call-status tone-${STATUS_TONE[status] ?? "muted"}`}>
              <span className="call-dot" />
              {status === "none" ? "idle" : status.replace(/_/g, " ")}
            </span>
          </div>

          <div className="call-transcript" ref={scrollRef}>
            {turns.length === 0 ? (
              <div className="call-empty">
                <Icon name="phone" size={26} />
                <strong>{active ? "Attached, waiting for a turn" : "No call in progress"}</strong>
                <p>
                  {active
                    ? status === "ringing"
                      ? "Answer to hear the opening digest, then reply in your own words."
                      : "Send an utterance below to continue this session."
                    : "Trigger a demo call to push a ring to authenticated phones and open the same session here."}
                </p>
                {active && status === "ringing" ? (
                  <button type="button" className="btn btn-primary" onClick={() => void speak()} disabled={busy !== null}>
                    {busy === "speak" ? <span className="spinner" aria-hidden="true" /> : <Icon name="play" size={14} />}
                    Answer & hear digest
                  </button>
                ) : null}
              </div>
            ) : (
              turns.map((turn) => (
                <div key={turn.id} className={`call-turn call-${turn.role}`}>
                  <span className="call-turn-role">{turn.role === "vioris" ? "Vioris" : turn.role === "you" ? "You" : "Session"}</span>
                  <p>{turn.text}</p>
                  {turn.meta ? <em>{turn.meta}</em> : null}
                </div>
              ))
            )}
          </div>

          {active ? (
            <div className="call-input">
              {status === "ringing" ? (
                <p className="call-hint">
                  <Icon name="phone" size={13} /> The line is ringing. Answer first to open the digest.
                </p>
              ) : null}
              <div className="call-input-row">
                <input
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  placeholder="Say something, e.g. “yes, go on” or “no that's all, bye”"
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) void send(text);
                  }}
                  disabled={busy !== null}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => void send(text)}
                  disabled={!text.trim() || busy !== null}
                >
                  {busy === "send" ? <span className="spinner" aria-hidden="true" /> : <Icon name="send" size={14} />}
                  Say
                </button>
              </div>
              <div className="call-quick">
                <button type="button" className="chip" onClick={() => void send("Yes, go on")} disabled={busy !== null}>
                  “Yes, go on”
                </button>
                <button
                  type="button"
                  className="chip"
                  onClick={() => void send("Which mails have I received? Any texts from Disha?")}
                  disabled={busy !== null}
                >
                  “Any mail or texts?”
                </button>
                <button
                  type="button"
                  className="chip"
                  onClick={() => void send("No that's all, bye")}
                  disabled={busy !== null}
                >
                  “That's all, bye”
                </button>
              </div>
            </div>
          ) : (
            <div className="call-idle-actions">
              <button type="button" className="btn btn-primary" onClick={() => void trigger()} disabled={busy !== null}>
                <Icon name="phone" size={15} /> Trigger a demo call
              </button>
              <span>Nothing is dialed on its own in v1.</span>
            </div>
          )}
        </section>

        <aside className="call-rail">
          <section className="panel">
            <div className="section-head">
              <div>
                <h2>Sessions</h2>
                <p>In-memory on the gateway. Restarting clears them.</p>
              </div>
              <button type="button" className="icon-btn" aria-label="Refresh calls" onClick={() => void loadCalls()}>
                <Icon name="refresh" size={15} />
              </button>
            </div>
            {calls.length === 0 ? (
              <p className="rail-empty">No call sessions yet.</p>
            ) : (
              <ul className="call-list">
                {calls.map((call) => (
                  <li key={call.call_id}>
                    <button
                      type="button"
                      className={`call-list-item ${call.call_id === activeId ? "is-active" : ""}`}
                      onClick={() => void focus(call)}
                    >
                      <span className="call-list-id">{call.call_id}</span>
                      <span className={`call-list-status tone-${STATUS_TONE[call.status] ?? "muted"}`}>
                        {call.status.replace(/_/g, " ")}
                      </span>
                      <span className="call-list-meta">
                        {call.turns} turns · {clockTime(call.created_at)} · {relativeTime(call.created_at)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <div className="section-head">
              <div>
                <h2>Call triggers</h2>
                <p>Your own reasons for Vioris to call.</p>
              </div>
              <button
                type="button"
                className="icon-btn"
                aria-label="Refresh call triggers"
                onClick={() => void loadTriggers()}
              >
                <Icon name="refresh" size={15} />
              </button>
            </div>
            {triggers.length === 0 ? (
              <p className="rail-empty">No triggers yet. Add one below.</p>
            ) : (
              <ul className="trigger-list">
                {triggers.map((trigger) => (
                  <li key={trigger.trigger_id} className="trigger-item">
                    <div>
                      <strong>
                        {trigger.label ||
                          (trigger.kind === "time"
                            ? `Every day at ${trigger.at}`
                            : `When “${trigger.match}” finishes`)}
                      </strong>
                      <span>
                        {trigger.one_shot ? "One-shot · " : ""}
                        {trigger.last_fired_at
                          ? `last fired ${relativeTime(trigger.last_fired_at)}`
                          : "not fired yet"}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`Delete trigger ${trigger.trigger_id}`}
                      onClick={() => void removeTrigger(trigger)}
                      disabled={busy !== null}
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="trigger-form">
              <div className="field-row">
                <label className="field">
                  <span>When</span>
                  <select
                    value={triggerKind}
                    onChange={(event) => setTriggerKind(event.target.value as CallTriggerKind)}
                  >
                    <option value="time">At a time</option>
                    <option value="task_completed">A task finishes</option>
                  </select>
                </label>
                {triggerKind === "time" ? (
                  <label className="field">
                    <span>Time</span>
                    <input
                      type="time"
                      value={triggerAt}
                      onChange={(event) => setTriggerAt(event.target.value)}
                    />
                  </label>
                ) : (
                  <label className="field">
                    <span>Task contains</span>
                    <input
                      value={triggerMatch}
                      placeholder="e.g. deploy"
                      onChange={(event) => setTriggerMatch(event.target.value)}
                    />
                  </label>
                )}
              </div>
              <label className="field">
                <span>Label (optional)</span>
                <input
                  value={triggerLabel}
                  placeholder="Check in in the evening"
                  maxLength={120}
                  onChange={(event) => setTriggerLabel(event.target.value)}
                />
              </label>
              <label className="switch-row">
                <input
                  type="checkbox"
                  checked={triggerOneShot}
                  onChange={(event) => setTriggerOneShot(event.target.checked)}
                />
                <span className="switch" aria-hidden="true" />
                <span>
                  <strong>Call once only</strong>
                  <em>Otherwise the trigger stays active until you delete it.</em>
                </span>
              </label>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => void addTrigger()}
                disabled={
                  busy !== null ||
                  (triggerKind === "time" ? !triggerAt : !triggerMatch.trim())
                }
              >
                <Icon name="plus" size={14} /> Add trigger
              </button>
            </div>
          </section>

          <section className="panel">
            <div className="section-head">
              <div>
                <h2>Ground rules</h2>
                <p>What this interface will not do.</p>
              </div>
            </div>
            <ul className="plain-list">
              <li>It does not dial anyone. Triggers open an in-app call, not a phone call.</li>
              <li>It does not synthesize audio here; the phone owns that surface.</li>
              <li>It does not send a message behind an approval gate.</li>
              <li>It does not keep a transcript after a gateway restart.</li>
            </ul>
            <div className="runtime-note">
              <Icon name="lock" size={14} />
              {system?.demo_mode
                ? "Demo connectors are on. Prepared actions are labelled, never delivered."
                : "No demo mode. Missing transports fail with a reason."}
            </div>
          </section>

          <section className="panel panel-quiet">
            <h2>What the phone hears</h2>
            <p>
              The same pipeline composes a short digest, answers compound requests, and asks for
              disambiguation before it addresses the wrong person. Replies are grounded in local
              data, not invented.
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}
