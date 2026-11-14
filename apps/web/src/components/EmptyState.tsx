import type { ReactNode } from "react";

import { Icon, type IconName } from "./Icon";

export function EmptyState({
  icon = "spark",
  title,
  body,
  action,
}: {
  icon?: IconName;
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={22} />
      </span>
      <h3>{title}</h3>
      <p>{body}</p>
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  intro,
  actions,
}: {
  eyebrow: string;
  title: string;
  intro?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {intro ? <p className="page-intro">{intro}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function SectionHead({
  title,
  note,
  action,
}: {
  title: string;
  note?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-head">
      <div>
        <h2>{title}</h2>
        {note ? <p>{note}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} style={{ width: `${88 - index * 12}%` }} />
      ))}
    </div>
  );
}
