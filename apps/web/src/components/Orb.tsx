import { useMemo } from "react";
import { OrbitalSphereBackground } from "@designcodeio/threeui/components/OrbitalSphereBackground";

import { usePrefs } from "../state/prefs";

export type OrbState =
  | "idle"
  | "listening"
  | "planning"
  | "awaiting"
  | "executing"
  | "speaking"
  | "stopped"
  | "error";

const STATE_COPY: Record<OrbState, { label: string; detail: string; hue: number }> = {
  idle: { label: "Idle", detail: "Listening for a request", hue: 0 },
  listening: { label: "Listening", detail: "Holding your words", hue: 140 },
  planning: { label: "Planning", detail: "Shaping the steps", hue: 32 },
  awaiting: { label: "Waiting on you", detail: "An approval is holding", hue: 78 },
  executing: { label: "Executing", detail: "Running an approved step", hue: 165 },
  speaking: { label: "Speaking", detail: "Delivering the digest", hue: 300 },
  stopped: { label: "Stopped", detail: "Everything is halted", hue: -20 },
  error: { label: "Needs attention", detail: "Something did not verify", hue: -45 },
};

export function Orb({
  state = "idle",
  label,
  detail,
  className = "",
}: {
  state?: OrbState;
  label?: string;
  detail?: string;
  className?: string;
}) {
  const { reducedMotion } = usePrefs();
  const config = STATE_COPY[state];

  const settings = useMemo(() => {
    switch (state) {
      case "listening":
        return { speed: 1.8, particleOpacity: 1, haloOpacity: 0.5, orbitOpacity: 0.5 };
      case "planning":
        return { speed: 1.2, particleOpacity: 0.9, haloOpacity: 0.4, orbitOpacity: 0.42 };
      case "awaiting":
        return { speed: 0.35, particleOpacity: 0.75, haloOpacity: 0.55, orbitOpacity: 0.3 };
      case "executing":
        return { speed: 2.4, particleOpacity: 0.95, haloOpacity: 0.55, orbitOpacity: 0.5 };
      case "speaking":
        return { speed: 0.9, particleOpacity: 0.9, haloOpacity: 0.6, orbitOpacity: 0.38 };
      case "stopped":
        return { speed: 0.04, particleOpacity: 0.3, haloOpacity: 0.12, orbitOpacity: 0.08 };
      case "error":
        return { speed: 0.2, particleOpacity: 0.55, haloOpacity: 0.28, orbitOpacity: 0.16 };
      default:
        return { speed: 0.5, particleOpacity: 0.85, haloOpacity: 0.4, orbitOpacity: 0.28 };
    }
  }, [state]);

  return (
    <div className={`orb orb-${state} ${className}`.trim()} data-state={state}>
      <div className="orb-visual" aria-hidden="true">
        {reducedMotion ? (
          <div className="orb-static" />
        ) : (
          <OrbitalSphereBackground
            className="orb-canvas"
            hue={config.hue}
            scale={0.94}
            particleSize={0.016}
            {...settings}
          />
        )}
        <div className="orb-vignette" />
        <div className="orb-ring orb-ring-a" />
        <div className="orb-ring orb-ring-b" />
        <div className="orb-scan" />
      </div>
      <div className="orb-readout">
        <span className="orb-state">
          <span className="orb-pulse" aria-hidden="true" />
          {label ?? config.label}
        </span>
        <span className="orb-detail">{detail ?? config.detail}</span>
      </div>
    </div>
  );
}
