import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Icon } from "./Icon";

type Tone = "info" | "ok" | "warn" | "bad";

interface ToastItem {
  id: number;
  title: string;
  message?: string;
  tone: Tone;
}

interface ToastApi {
  push: (toast: { title: string; message?: string; tone?: Tone }) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const TONE_ICON = {
  info: "spark",
  ok: "check",
  warn: "alert",
  bad: "alert",
} as const;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
    const timer = timers.current.get(id);
    if (timer) window.clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const push = useCallback(
    ({ title, message, tone = "info" }: { title: string; message?: string; tone?: Tone }) => {
      const id = ++counter.current;
      setItems((current) => [...current.slice(-3), { id, title, message, tone }]);
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), tone === "bad" ? 9000 : 5500),
      );
    },
    [dismiss],
  );

  useEffect(() => {
    const active = timers.current;
    return () => {
      active.forEach((timer) => window.clearTimeout(timer));
      active.clear();
    };
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`toast toast-${item.tone}`}>
            <span className="toast-icon">
              <Icon name={TONE_ICON[item.tone]} size={16} />
            </span>
            <div className="toast-copy">
              <strong>{item.title}</strong>
              {item.message ? <span>{item.message}</span> : null}
            </div>
            <button
              type="button"
              className="icon-btn icon-btn-quiet toast-close"
              aria-label="Dismiss notification"
              onClick={() => dismiss(item.id)}
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
