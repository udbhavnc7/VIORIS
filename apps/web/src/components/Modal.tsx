import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { Icon } from "./Icon";

export function Modal({
  open,
  onClose,
  title,
  eyebrow,
  children,
  footer,
  size = "md",
  dismissable = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  eyebrow?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  dismissable?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && dismissable) onClose();
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const target = panel.current?.querySelector<HTMLElement>(
      "input, textarea, select, button:not(.modal-close)",
    );
    window.setTimeout(() => target?.focus(), 40);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open, onClose, dismissable]);

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && dismissable && onClose()}>
      <div
        ref={panel}
        className={`modal-panel modal-${size}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="modal-head">
          <div>
            {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
            <h2>{title}</h2>
          </div>
          {dismissable ? (
            <button type="button" className="icon-btn modal-close" aria-label="Close" onClick={onClose}>
              <Icon name="x" size={18} />
            </button>
          ) : null}
        </header>
        <div className="modal-body">{children}</div>
        {footer ? <footer className="modal-foot">{footer}</footer> : null}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  tone = "danger",
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  tone?: "danger" | "primary";
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      size="sm"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={`btn ${tone === "danger" ? "btn-danger" : "btn-primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            {confirmLabel}
          </button>
        </>
      }
    >
      <div className="confirm-body">{body}</div>
    </Modal>
  );
}
