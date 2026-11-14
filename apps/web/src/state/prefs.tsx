import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export interface Prefs {
  autoRefresh: boolean;
  reduceMotion: "system" | "on" | "off";
  showIds: boolean;
}

const DEFAULTS: Prefs = { autoRefresh: true, reduceMotion: "system", showIds: false };
const KEY = "vioris.prefs.v1";

interface PrefsState {
  prefs: Prefs;
  update: (patch: Partial<Prefs>) => void;
  reducedMotion: boolean;
}

const PrefsContext = createContext<PrefsState | null>(null);

function load(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Prefs>) };
  } catch {
    return DEFAULTS;
  }
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>(load);
  const [systemReduced, setSystemReduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!media) return;
    const listener = (event: MediaQueryListEvent) => setSystemReduced(event.matches);
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, []);

  const update = useCallback((patch: Partial<Prefs>) => {
    setPrefs((current) => {
      const next = { ...current, ...patch };
      localStorage.setItem(KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const reducedMotion =
    prefs.reduceMotion === "on" || (prefs.reduceMotion === "system" && systemReduced);

  const value = useMemo(
    () => ({ prefs, update, reducedMotion }),
    [prefs, update, reducedMotion],
  );
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePrefs(): PrefsState {
  const context = useContext(PrefsContext);
  if (!context) throw new Error("usePrefs must be used inside PrefsProvider");
  return context;
}
