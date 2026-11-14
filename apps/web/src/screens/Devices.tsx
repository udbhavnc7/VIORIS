import { useCallback, useEffect, useMemo, useState } from "react";

import { api, formatError, loadSession, type Device } from "../lib/api";
import { clockTime, relativeTime } from "../lib/format";
import { useData } from "../state/store";
import { PageHeader } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { ConfirmDialog } from "../components/Modal";
import { useToast } from "../components/Toast";

export function Devices() {
  const { connected, health, refreshSystem } = useData();
  const { push } = useToast();
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<Device | null>(null);
  const [revoking, setRevoking] = useState(false);

  const session = useMemo(() => loadSession(), []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDevices(await api.devices());
      setError(null);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = async () => {
    if (!target) return;
    setRevoking(true);
    try {
      await api.revokeDevice(target.device_id);
      push({
        title: "Device revoked",
        message:
          target.device_id === session?.deviceId
            ? "This browser's key was revoked. It will be sent back to pairing on the next call."
            : "Its key can no longer authenticate.",
        tone: "warn",
      });
      setTarget(null);
      await load();
      await refreshSystem();
    } catch (err) {
      push({ title: "Could not revoke", message: formatError(err), tone: "bad" });
    } finally {
      setRevoking(false);
    }
  };

  const active = devices.filter((device) => !device.revoked);

  return (
    <div className="page devices-page">
      <PageHeader
        eyebrow="Access control"
        title="Who can reach this machine"
        intro="Every paired device holds its own scoped key. Revoking one never touches another, and a revoked key fails closed — there is no soft fallback."
        actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void load()}>
            <Icon name="refresh" size={14} /> Refresh
          </button>
        }
      />

      <div className="device-stats">
        <div>
          <strong>{active.length}</strong>
          <span>active pairings</span>
        </div>
        <div>
          <strong>{connected?.phones ?? 0}</strong>
          <span>phone sockets</span>
        </div>
        <div>
          <strong>{connected?.laptops ?? 0}</strong>
          <span>laptop sockets</span>
        </div>
        <div>
          <strong>{health?.paired_devices ?? active.length}</strong>
          <span>gateway records</span>
        </div>
      </div>

      {error ? (
        <div className="callout callout-bad">
          <Icon name="alert" size={16} />
          <div>
            <strong>Device registry did not answer</strong>
            <span>{error}</span>
          </div>
        </div>
      ) : null}

      <div className="devices-layout">
        <section className="panel devices-panel">
          <div className="section-head">
            <div>
              <h2>Paired devices</h2>
              <p>Keys are device-scoped. Revocation is immediate.</p>
            </div>
            <span className="mini-chip">
              <Icon name="lock" size={12} /> {health?.pairing_enabled ? "pairing enabled" : "pairing closed"}
            </span>
          </div>

          {loading && devices.length === 0 ? (
            <div className="rolling">
              <span className="spinner" /> Reading the device registry…
            </div>
          ) : devices.length === 0 ? (
            <p className="rail-empty">No devices are paired yet.</p>
          ) : (
            <ul className="device-list">
              {devices.map((device) => {
                const current = device.device_id === session?.deviceId;
                return (
                  <li className={`device-row ${current ? "is-current" : ""} ${device.revoked ? "is-revoked" : ""}`} key={device.device_id}>
                    <span className={`device-glyph ${current ? "is-this" : ""}`}>
                      <Icon name={current ? "monitor" : "phone"} size={17} />
                    </span>
                    <div className="device-info">
                      <strong>
                        {device.name}
                        {current ? <span className="device-you">this browser</span> : null}
                        {device.revoked ? <span className="device-revoked">revoked</span> : null}
                      </strong>
                      <span>
                        paired {relativeTime(device.created_at)} · <code>{device.device_id.slice(0, 10)}…</code>
                      </span>
                    </div>
                    {device.revoked ? (
                      <span className="mini-status is-bad">no access</span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-danger-ghost btn-xs"
                        onClick={() => setTarget(device)}
                      >
                        Revoke
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <aside className="devices-rail">
          <section className="panel">
            <div className="section-head">
              <div>
                <h2>Pair a phone</h2>
                <p>No public port. Tailscale only.</p>
              </div>
            </div>
            <ol className="pair-steps">
              <li>
                <span>01</span>
                <div>
                  <strong>Join the same tailnet</strong>
                  <p>Install Tailscale on the laptop and the phone. Keep the gateway on loopback.</p>
                </div>
              </li>
              <li>
                <span>02</span>
                <div>
                  <strong>Start the gateway on the private interface</strong>
                  <p>
                    <code>python run_vioris.py --host 0.0.0.0</code> is only acceptable on a
                    Tailscale address. Never expose it publicly.
                  </p>
                </div>
              </li>
              <li>
                <span>03</span>
                <div>
                  <strong>Pair from the phone</strong>
                  <p>
                    Open the gateway’s <code>/pair</code> page at the Tailscale IP on port{" "}
                    <code>8420</code>, then exchange the one-time token within ten minutes.
                  </p>
                </div>
              </li>
            </ol>
            <div className="callout callout-info">
              <Icon name="lock" size={15} />
              <div>
                <strong>The token is shown once.</strong>
                <span>It expires quickly and is bound to one device record. Do not paste it into a chat.</span>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="section-head">
              <div>
                <h2>This browser</h2>
                <p>Where the UI key lives.</p>
              </div>
            </div>
            <div className="session-facts">
              <div>
                <span>Device</span>
                <strong>{session?.name ?? "not paired"}</strong>
              </div>
              <div>
                <span>Scoped id</span>
                <code>{session?.deviceId.slice(0, 12) ?? "—"}…</code>
              </div>
              <div>
                <span>Paired at</span>
                <strong>
                  {session ? `${clockTime(session.createdAt)} · ${relativeTime(session.createdAt)}` : "—"}
                </strong>
              </div>
            </div>
            <p className="fine-print">
              The key is held in this browser’s local storage and is never rendered in the page or
              sent to any service except the local gateway.
            </p>
          </section>

          <section className="panel panel-quiet">
            <h2>Revocation semantics</h2>
            <ul className="plain-list">
              <li>A revoked key is rejected by REST and by the device socket.</li>
              <li>Other devices are untouched.</li>
              <li>Remote sessions owned by a revoked device cannot continue.</li>
              <li>Pairing again creates a new record, not a resurrected one.</li>
            </ul>
          </section>
        </aside>
      </div>

      <ConfirmDialog
        open={Boolean(target)}
        title={target?.device_id === session?.deviceId ? "Revoke this browser?" : "Revoke this device?"}
        body={
          <div className="confirm-copy">
            <p>
              <strong>{target?.name}</strong> will immediately lose access. Any request it makes
              afterward fails closed.
            </p>
            {target?.device_id === session?.deviceId ? (
              <p className="fine-print">
                This is the browser you are using. You will be returned to the pairing screen.
              </p>
            ) : null}
          </div>
        }
        confirmLabel="Revoke access"
        busy={revoking}
        onCancel={() => setTarget(null)}
        onConfirm={() => void revoke()}
      />
    </div>
  );
}
