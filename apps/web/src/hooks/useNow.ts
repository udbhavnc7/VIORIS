import { useEffect, useState } from "react";

/** A ticking clock for countdowns and relative timestamps. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = title === "Overview" ? "Vioris" : `${title} · Vioris`;
  }, [title]);
}
