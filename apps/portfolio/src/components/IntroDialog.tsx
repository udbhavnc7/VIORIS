import { useEffect, useRef } from 'react';
import { profile } from '../data/profile';
import { Icon } from './Icon';

type IntroDialogProps = {
  open: boolean;
  onClose: () => void;
};

export function IntroDialog({ open, onClose }: IntroDialogProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previousActive = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [],
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
      previousActive?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        ref={dialogRef}
        className="intro-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="intro-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button ref={closeRef} className="dialog-close" type="button" onClick={onClose} aria-label="Close introduction">
          <Icon name="close" size={20} />
        </button>
        <p className="eyebrow"><span aria-hidden="true" /> Quick introduction</p>
        <h2 id="intro-dialog-title">Hi, I’m {profile.firstName}.</h2>
        <p className="intro-dialog__role">{profile.role}</p>
        <p>{profile.intro}</p>
        <div className="intro-dialog__facts">
          <span><Icon name="map-pin" size={16} /> {profile.location}</span>
          <span><Icon name="code" size={16} /> {profile.stats.projects} selected projects</span>
          <span><Icon name="award" size={16} /> {profile.stats.hackathonMilestones} hackathon milestones</span>
        </div>
        <div className="intro-dialog__actions">
          <a className="button button--small" href={profile.github} target="_blank" rel="noreferrer">
            <Icon name="github" size={16} /> GitHub
          </a>
          <a className="button button--small button--ghost" href={`mailto:${profile.email}`}>
            <Icon name="mail" size={16} /> Email
          </a>
        </div>
      </div>
    </div>
  );
}
