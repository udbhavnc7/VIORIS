import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  api,
  formatError,
  loadSession,
  pairBrowser,
  type GatewayHealth,
  type Session,
} from "../lib/api";
import { Orb } from "../components/Orb";
import { Icon } from "../components/Icon";
import { LiveDot } from "../components/Pills";
import { usePageTitle } from "../hooks/useNow";

const SAFETY_POINTS = [
  {
    icon: "lock",
    title: "A key for this browser only",
    body: "Pairing issues a one-time token, then a device-scoped key. The key stays in this browser and can be revoked from Devices.",
  },
  {
    icon: "shield",
    title: "The gateway stays the authority",
    body: "This interface can only ask. Risk is assigned by a frozen registry, and Execute or Critical steps wait for your explicit approval.",
  },
  {
    icon: "wifi",
    title: "Loopback, unless you opt in",
    body: "The UI host answers on a local hostname only. Reach it from a phone over Tailscale — never by opening a public port.",
  },
] as const;

export function Pair({ onPaired }: { onPaired: (session: Session) => void }) {
  usePageTitle("Pair");
  const [name, setName] = useState(() => {
    const existing = loadSession();
    if (existing?.name) return existing.name;
    return "This browser";
  });
  const [health, setHealth] = useState<GatewayHealth | null>(null);
  const [probeState, setProbeState] = useState<"checking" | "online" | "offline">("checking");
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const probe = useCallback(async () => {
    setProbeState("checking");
    try {
      const next = await api.health();
      setHealth(next);
      setProbeState("online");
    } catch {
      setHealth(null);
      setProbeState("offline");
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setStep("Requesting a device pairing…");
    try {
      const session = await pairBrowser(trimmed, (next) => setStep(next));
      setStep("Paired. Opening your command center…");
      onPaired(session);
    } catch (err) {
      setError(formatError(err));
      setStep(null);
      setBusy(false);
      void probe();
    }
  };

  const offline = probeState === "offline";

  return (
    <div className="pair-frame">
      <div className="pair-atmos" aria-hidden="true">
        <Orb state={busy ? "planning" : "idle"} />
      </div>

      <div className="pair-layout">
        <section className="pair-intro">
          <div className="brand brand-large">
            <span className="brand-mark" aria-hidden="true">
              <span className="brand-orbit" />
              <span className="brand-core" />
            </span>
            <span className="brand-copy">
              <strong>VIORIS</strong>
              <em>Personal agent</em>
            </span>
          </div>

          <h1>
            Observe broadly.
            <br />
            Act narrowly.
            <br />
            <span>Never act invisibly.</span>
          </h1>

          <p className="pair-lede">
            A local-first operating agent. It listens, plans, and then stops at the exact point where
            an action would change something outside this machine — and waits for you.
          </p>

          <ul className="pair-points">
            {SAFETY_POINTS.map((point) => (
              <li key={point.title}>
                <span className="pair-point-icon">
                  <Icon name={point.icon} size={16} />
                </span>
                <div>
                  <strong>{point.title}</strong>
                  <p>{point.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="pair-card panel">
          <div className="pair-card-head">
            <span className="eyebrow">Device pairing</span>
            <span className={`pair-health ${offline ? "is-off" : probeState === "online" ? "is-ok" : ""}`}>
              <LiveDot live={probeState === "online"} />
              {probeState === "checking"
                ? "Checking the gateway…"
                : probeState === "online"
                  ? "Gateway online"
                  : "Gateway unreachable"}
            </span>
          </div>

          <h2>Authorize this browser</h2>
          <p className="pair-card-intro">
            Pairing asks the local gateway for a short-lived token, immediately exchanges it for a
            device-scoped key, and keeps that key here. No password is stored and nothing leaves this
            machine.
          </p>

          {health ? (
            <div className="pair-facts">
              <span>
                <strong>{health.tools_registered}</strong>
                <em>tools in the registry</em>
              </span>
              <span>
                <strong>{health.registry_frozen ? "Frozen" : "Mutable"}</strong>
                <em>permission authority</em>
              </span>
              <span>
                <strong>{health.paired_devices}</strong>
                <em>paired devices</em>
              </span>
            </div>
          ) : null}

          <form onSubmit={submit} className="pair-form">
            <label htmlFor="device-name">Name this device</label>
            <div className="input-with-icon">
              <Icon name="devices" size={16} />
              <input
                id="device-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Ud's laptop"
                maxLength={64}
                autoComplete="off"
                disabled={busy}
              />
            </div>
            <p className="fine-print">
              Shown in the device registry and in the audit chain. Pick something you will recognize.
            </p>

            {step ? (
              <div className="pair-progress" role="status">
                {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="check" size={14} />}
                <span>{step}</span>
              </div>
            ) : null}

            {error ? (
              <div className="inline-alert inline-alert-bad" role="alert">
                <Icon name="alert" size={15} />
                <div>
                  <strong>Pairing did not complete</strong>
                  <span>{error}</span>
                </div>
              </div>
            ) : null}

            {offline ? (
              <div className="inline-alert inline-alert-warn">
                <Icon name="wifiOff" size={15} />
                <div>
                  <strong>The local gateway is not answering</strong>
                  <span>
                    Start it from the project root with <code>python run_vioris.py</code>, then try
                    again. This page talks to nothing on the internet.
                  </span>
                </div>
              </div>
            ) : null}

            <button
              type="submit"
              className="btn btn-primary btn-wide"
              disabled={busy || !name.trim() || offline}
            >
              {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="shield" size={15} />}
              <span>{busy ? "Pairing…" : "Pair this browser"}</span>
            </button>

            <button
              type="button"
              className="btn btn-ghost btn-wide"
              onClick={() => void probe()}
              disabled={busy}
            >
              <Icon name="refresh" size={14} />
              <span>Check the gateway again</span>
            </button>
          </form>

          <p className="pair-footnote">
            <Icon name="lock" size={12} />
            A pairing token is valid for ten minutes and can be used once.
          </p>
        </section>
      </div>
    </div>
  );
}
