import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatError, type RemoteSession } from "../lib/api";
import { relativeTime } from "../lib/format";
import { useData } from "../state/store";
import { PageHeader } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { ConfirmDialog } from "../components/Modal";
import { useToast } from "../components/Toast";

const TIMEOUTS = [5, 10, 15, 30];

export function Remote() {
  const { connections, refreshAll } = useData();
  const { push } = useToast();
  const navigate = useNavigate();

  const [session, setSession] = useState<RemoteSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("phone");
  const [minutes, setMinutes] = useState(5);
  const [starting, setStarting] = useState(false);

  const [frame, setFrame] = useState<string | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);

  const [text, setText] = useState("");
  const [x, setX] = useState("640");
  const [y, setY] = useState("360");
  const [inputBusy, setInputBusy] = useState<string | null>(null);

  const [lockOpen, setLockOpen] = useState(false);
  const [ending, setEnding] = useState(false);

  const computer = connections?.connections.find((item) => item.id === "computer");
  const agentUp = Boolean(computer?.configured);

  const loadSession = useCallback(async () => {
    try {
      const active = await api.remoteSession();
      setSession(active);
      setError(null);
    } catch (err) {
      setSession(null);
      setError(formatError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSession();
  }, [loadSession]);

  useEffect(() => {
    const timer = window.setInterval(() => void loadSession(), 6000);
    return () => window.clearInterval(timer);
  }, [loadSession]);

  useEffect(
    () => () => {
      if (frame) URL.revokeObjectURL(frame);
    },
    [frame],
  );

  const start = async () => {
    setStarting(true);
    try {
      const result = await api.startRemote(name.trim() || "phone", minutes);
      push({
        title: "Session request created",
        message: "Review the diff and approve it before any input path opens.",
        tone: "warn",
      });
      await refreshAll();
      navigate("/approvals");
      return result;
    } catch (err) {
      push({ title: "Could not request a session", message: formatError(err), tone: "bad" });
    } finally {
      setStarting(false);
    }
  };

  const capture = async () => {
    setCapturing(true);
    setFrameError(null);
    try {
      const url = await api.screenshot();
      setFrame((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return url;
      });
    } catch (err) {
      setFrameError(formatError(err));
    } finally {
      setCapturing(false);
    }
  };

  const sendInput = async (action: "type" | "left" | "right" | "move") => {
    if (!session) return;
    setInputBusy(action);
    try {
      await api.remoteInput({
        session_id: session.session_id,
        action,
        text: action === "type" ? text : undefined,
        x: action === "type" ? undefined : Number(x) || 0,
        y: action === "type" ? undefined : Number(y) || 0,
      });
      push({ title: `Sent ${action} input`, tone: "ok" });
      if (action === "type") setText("");
    } catch (err) {
      push({ title: "Input refused", message: formatError(err), tone: "bad" });
    } finally {
      setInputBusy(null);
    }
  };

  const end = async () => {
    setEnding(true);
    try {
      await api.endRemote();
      setSession(null);
      setFrame((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return null;
      });
      push({ title: "Remote session ended", tone: "warn" });
      await loadSession();
    } catch (err) {
      push({ title: "Could not end the session", message: formatError(err), tone: "bad" });
    } finally {
      setEnding(false);
    }
  };

  const lock = async () => {
    setEnding(true);
    try {
      await api.lockRemote();
      setSession(null);
      setLockOpen(false);
      push({ title: "Laptop locked", message: "The session ended with it.", tone: "warn" });
      await loadSession();
    } catch (err) {
      push({ title: "Lock failed", message: formatError(err), tone: "bad" });
    } finally {
      setEnding(false);
    }
  };

  return (
    <div className="page remote-page">
      <PageHeader
        eyebrow="Gated control"
        title="A window into this laptop"
        intro="Starting a session is an Execute action, not a switch. You review the exact device and duration, approve it, and only then can a single frame or input cross the boundary."
        actions={
          <span className={`page-chip ${agentUp ? "is-ok" : "is-warn"}`}>
            <Icon name={agentUp ? "monitor" : "alert"} size={14} />
            {agentUp ? "Computer agent reachable" : "Computer agent offline"}
          </span>
        }
      />

      {!agentUp ? (
        <div className="callout callout-warn">
          <Icon name="alert" size={16} />
          <div>
            <strong>The computer agent is not answering.</strong>
            <span>
              You can still see how the gate works, but a capture or input will be refused until the
              agent is running on this laptop.
            </span>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="callout callout-bad">
          <Icon name="alert" size={16} />
          <div>
            <strong>Session state could not be read</strong>
            <span>{error}</span>
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => void loadSession()}>
              Retry
            </button>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="rolling">
          <span className="spinner" /> Checking for a live session…
        </div>
      ) : session ? (
        <div className="remote-live">
          <section className="remote-screen-panel">
            <div className="remote-screen-head">
              <div>
                <h2>Live frame</h2>
                <p>
                  Captured on demand. This is a peek, never a continuous stream — at most one frame
                  every couple of seconds.
                </p>
              </div>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => void capture()} disabled={capturing}>
                {capturing ? <span className="spinner" aria-hidden="true" /> : <Icon name="screen" size={14} />}
                Capture frame
              </button>
            </div>
            <div className="remote-frame">
              {frame ? (
                <img src={frame} alt="Current laptop screen" />
              ) : (
                <div className="remote-frame-empty">
                  <Icon name="monitor" size={28} />
                  <strong>No frame captured yet</strong>
                  <span>The screen stays private until you ask for one frame.</span>
                </div>
              )}
            </div>
            {frameError ? <p className="inline-error">{frameError}</p> : null}
            <div className="remote-screen-foot">
              <span>
                <Icon name="lock" size={13} /> Session {session.session_id.slice(0, 8)} · expires{" "}
                {session.expires_at ? relativeTime(session.expires_at) : "on its own"}
              </span>
              <div className="remote-screen-actions">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLockOpen(true)}>
                  <Icon name="lock" size={13} /> Lock now
                </button>
                <button type="button" className="btn btn-danger-ghost btn-sm" onClick={() => void end()} disabled={ending}>
                  {ending ? <span className="spinner" aria-hidden="true" /> : <Icon name="x" size={13} />}
                  End session
                </button>
              </div>
            </div>
          </section>

          <aside className="remote-controls">
            <section className="panel">
              <div className="section-head">
                <div>
                  <h2>Input</h2>
                  <p>Input only lands inside a live, approved session.</p>
                </div>
              </div>
              <label className="field">
                <span>Type text</span>
                <div className="inline-field">
                  <input
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder="Hello from the phone"
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && text) void sendInput("type");
                    }}
                  />
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={!text || inputBusy !== null}
                    onClick={() => void sendInput("type")}
                  >
                    {inputBusy === "type" ? <span className="spinner" aria-hidden="true" /> : <Icon name="send" size={13} />}
                    Type
                  </button>
                </div>
              </label>

              <div className="field-row">
                <label className="field">
                  <span>X</span>
                  <input value={x} inputMode="numeric" onChange={(event) => setX(event.target.value)} />
                </label>
                <label className="field">
                  <span>Y</span>
                  <input value={y} inputMode="numeric" onChange={(event) => setY(event.target.value)} />
                </label>
              </div>
              <div className="pointer-actions">
                <button type="button" className="btn btn-ghost btn-sm" disabled={inputBusy !== null} onClick={() => void sendInput("move")}>
                  <Icon name="arrowUpRight" size={13} /> Move
                </button>
                <button type="button" className="btn btn-ghost btn-sm" disabled={inputBusy !== null} onClick={() => void sendInput("left")}>
                  Left click
                </button>
                <button type="button" className="btn btn-ghost btn-sm" disabled={inputBusy !== null} onClick={() => void sendInput("right")}>
                  Right click
                </button>
              </div>
              <p className="fine-print">
                Coordinates are pixels on this laptop’s primary display. The gateway re-checks the
                session on every input.
              </p>
            </section>

            <section className="panel panel-quiet">
              <h2>Stop paths</h2>
              <p>
                “Lock now” locks the laptop and ends the session in one step. “Stop everything” on
                any screen also ends every remote session for this device.
              </p>
            </section>
          </aside>
        </div>
      ) : (
        <div className="remote-idle">
          <section className="remote-illustration">
            <div className="screen-frame">
              <div className="screen-bar">
                <span />
                <span />
                <span />
                <em>this laptop</em>
              </div>
              <div className="screen-body">
                <span className="screen-line long" />
                <span className="screen-line" />
                <span className="screen-line short" />
                <div className="screen-lock">
                  <Icon name="lock" size={22} />
                  <strong>No session</strong>
                  <span>Screen and input stay shut.</span>
                </div>
              </div>
            </div>
            <div className="remote-steps">
              <div>
                <span>01</span>
                <p>Request a time-boxed session for this device.</p>
              </div>
              <div>
                <span>02</span>
                <p>Approve the exact device and duration in the queue.</p>
              </div>
              <div>
                <span>03</span>
                <p>Peek at one frame, or send input, until it expires.</p>
              </div>
            </div>
          </section>

          <section className="panel remote-request">
            <div className="section-head">
              <div>
                <h2>Request a session</h2>
                <p>This creates an Execute-tier task. Nothing opens before your approval.</p>
              </div>
            </div>
            <label className="field">
              <span>Device label</span>
              <input value={name} onChange={(event) => setName(event.target.value)} maxLength={60} />
            </label>
            <div className="field">
              <span>Duration</span>
              <div className="segmented">
                {TIMEOUTS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={minutes === value ? "is-active" : ""}
                    onClick={() => setMinutes(value)}
                  >
                    {value} min
                  </button>
                ))}
              </div>
            </div>
            <button type="button" className="btn btn-primary btn-block" onClick={() => void start()} disabled={starting}>
              {starting ? <span className="spinner" aria-hidden="true" /> : <Icon name="shield" size={16} />}
              Request approval
            </button>
            <p className="fine-print">
              A session is scoped to this paired device. Ending it or locking the laptop shuts the
              input path immediately.
            </p>
          </section>
        </div>
      )}

      <ConfirmDialog
        open={lockOpen}
        title="Lock this laptop now?"
        body={
          <div className="confirm-copy">
            <p>
              The laptop locks immediately and the remote session ends. No further input can flow
              from this device until you approve a new session.
            </p>
          </div>
        }
        confirmLabel="Lock & end"
        tone="primary"
        busy={ending}
        onCancel={() => setLockOpen(false)}
        onConfirm={() => void lock()}
      />
    </div>
  );
}
