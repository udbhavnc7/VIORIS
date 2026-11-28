import { useMemo } from 'react';
import { profile } from '../data/profile';

type OrbitalPortraitProps = {
  onPlay?: () => void;
  compact?: boolean;
};

function OrbitalDots() {
  const dots = useMemo(() => {
    const result: Array<{ x: number; y: number; r: number; opacity: number }> = [];
    const rings = [
      { rx: 278, ry: 252, count: 150, offset: 0 },
      { rx: 246, ry: 222, count: 120, offset: 0.02 },
      { rx: 214, ry: 192, count: 92, offset: 0.05 },
    ];
    for (const ring of rings) {
      for (let i = 0; i < ring.count; i++) {
        const angle = (i / ring.count) * Math.PI * 2 + ring.offset;
        const wobble = 1 + Math.sin(i * 2.17) * 0.012;
        result.push({
          x: 300 + Math.cos(angle) * ring.rx * wobble,
          y: 300 + Math.sin(angle) * ring.ry * wobble,
          r: i % 9 === 0 ? 1.35 : i % 4 === 0 ? 1 : 0.7,
          opacity: 0.22 + ((i * 7) % 10) / 16,
        });
      }
    }
    return result;
  }, []);

  return (
    <svg className="orbital-dots" viewBox="0 0 600 600" aria-hidden="true">
      <circle cx="300" cy="300" r="286" fill="none" stroke="currentColor" strokeOpacity="0.08" />
      {dots.map((dot, index) => (
        <circle key={index} cx={dot.x} cy={dot.y} r={dot.r} fill="currentColor" opacity={dot.opacity} />
      ))}
    </svg>
  );
}

export function OrbitalPortrait({ onPlay, compact = false }: OrbitalPortraitProps) {
  return (
    <div className={`orbital-portrait ${compact ? 'orbital-portrait--compact' : ''}`}>
      <div className="orbital-portrait__glow" aria-hidden="true" />
      <OrbitalDots />
      <div className="portrait-card">
        <span className="portrait-card__corner portrait-card__corner--tl" aria-hidden="true" />
        <span className="portrait-card__corner portrait-card__corner--tr" aria-hidden="true" />
        <span className="portrait-card__corner portrait-card__corner--bl" aria-hidden="true" />
        <span className="portrait-card__corner portrait-card__corner--br" aria-hidden="true" />
        <div className="portrait-card__texture" aria-hidden="true" />
        <div className="portrait-card__monogram" aria-label={`${profile.name} monogram`}>
          <span>{profile.initials}</span>
        </div>
        <p className="portrait-card__label">CS · DATA SCIENCE</p>
        {onPlay ? (
          <button className="portrait-card__play" type="button" onClick={onPlay} aria-label="Play portfolio introduction">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 7 8 5-8 5V7Z" fill="currentColor" />
            </svg>
          </button>
        ) : null}
      </div>
      <div className="orbital-portrait__caption">
        <span className="status-dot" aria-hidden="true" />
        Building with purpose
      </div>
    </div>
  );
}
