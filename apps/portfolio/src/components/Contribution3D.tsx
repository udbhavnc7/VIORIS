import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { ContributionDay } from '../lib/contributions';
import { buildContributionWeeks } from '../lib/contributions';
import { Icon } from './Icon';

type Contribution3DProps = {
  open: boolean;
  onClose: () => void;
  total: number;
  days: ContributionDay[];
};

const palette = [
  { top: '#242924', left: '#171a17', right: '#111311' },
  { top: '#0e4429', left: '#0b3520', right: '#082819' },
  { top: '#006d32', left: '#005527', right: '#00441f' },
  { top: '#26a641', left: '#1d8534', right: '#166b2a' },
  { top: '#39d353', left: '#2eb84a', right: '#24973c' },
];

export function Contribution3D({ open, onClose, total, days }: Contribution3DProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

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
      if (event.key === 'Tab') {
        event.preventDefault();
        closeRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
      previousActive?.focus();
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;

    const weeks = buildContributionWeeks(days);
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let width = window.innerWidth;
    let height = window.innerHeight;
    let frame = 0;
    let pointerX = 0;
    let pointerY = 0;
    let currentX = 0;
    let currentY = 0;

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reducedMotion) queueDraw();
    };

    const onPointerMove = (event: PointerEvent) => {
      pointerX = (event.clientX / width - 0.5) * 2;
      pointerY = (event.clientY / height - 0.5) * 2;
      if (reducedMotion) queueDraw();
    };

    const drawPolygon = (points: Array<[number, number]>, fill: string) => {
      context.beginPath();
      context.moveTo(points[0][0], points[0][1]);
      for (let index = 1; index < points.length; index++) context.lineTo(points[index][0], points[index][1]);
      context.closePath();
      context.fillStyle = fill;
      context.fill();
    };

    const draw = (time: number) => {
      if (reducedMotion) {
        currentX = pointerX;
        currentY = pointerY;
      } else {
        currentX += (pointerX - currentX) * 0.06;
        currentY += (pointerY - currentY) * 0.06;
      }
      const angle = currentX * 0.09 + (reducedMotion ? 0 : Math.sin(time * 0.00018) * 0.025);
      const cosine = Math.cos(angle);
      const sine = Math.sin(angle);
      const cols = weeks.length;
      const rows = 7;
      const horizontalSpace = Math.max(240, width - (width < 600 ? 48 : 160));
      const verticalSpace = Math.max(240, height - 180);
      const tileWidth = Math.max(3.5, Math.min(18, horizontalSpace / (cols + rows + 2), verticalSpace / ((cols + rows) * 0.6 + 4)));
      const tileHeight = tileWidth * 0.5;
      const centerX = width / 2;
      const centerY = height / 2 + (width < 600 ? -10 : 30);

      context.clearRect(0, 0, width, height);
      context.fillStyle = '#0a0a0a';
      context.fillRect(0, 0, width, height);

      const glow = context.createRadialGradient(centerX, centerY, 0, centerX, centerY, Math.max(width, height) * 0.55);
      glow.addColorStop(0, 'rgba(57, 211, 83, 0.07)');
      glow.addColorStop(0.55, 'rgba(57, 211, 83, 0.02)');
      glow.addColorStop(1, 'rgba(10, 10, 10, 0)');
      context.fillStyle = glow;
      context.fillRect(0, 0, width, height);

      const cubes: Array<{ x: number; z: number; level: number; depth: number }> = [];
      weeks.forEach((week, col) => {
        week.forEach((day, row) => {
          const level = Math.max(0, Math.min(4, day?.level ?? 0));
          const rawX = col - (cols - 1) / 2;
          const rawZ = row - (rows - 1) / 2;
          const x = rawX * cosine - rawZ * sine;
          const z = rawX * sine + rawZ * cosine;
          cubes.push({ x, z, level, depth: x + z });
        });
      });
      cubes.sort((a, b) => a.depth - b.depth);

      for (const cube of cubes) {
        const screenX = centerX + (cube.x - cube.z) * tileWidth;
        const screenY = centerY + (cube.x + cube.z) * tileHeight + currentY * 8;
        const cubeHeight = 2 + cube.level * tileWidth * 0.42;
        const halfWidth = tileWidth / 2;
        const halfHeight = tileHeight / 2;
        const colors = palette[cube.level];
        const topY = screenY - cubeHeight;

        if (cube.level === 4) {
          context.shadowColor = 'rgba(57, 211, 83, 0.45)';
          context.shadowBlur = 12;
        } else {
          context.shadowBlur = 0;
        }

        drawPolygon(
          [
            [screenX - halfWidth, topY + halfHeight],
            [screenX, topY + halfHeight * 2],
            [screenX, screenY + halfHeight * 2],
            [screenX - halfWidth, screenY + halfHeight],
          ],
          colors.left,
        );
        drawPolygon(
          [
            [screenX, topY + halfHeight * 2],
            [screenX + halfWidth, topY + halfHeight],
            [screenX + halfWidth, screenY + halfHeight],
            [screenX, screenY + halfHeight * 2],
          ],
          colors.right,
        );
        drawPolygon(
          [
            [screenX, topY],
            [screenX + halfWidth, topY + halfHeight],
            [screenX, topY + halfHeight * 2],
            [screenX - halfWidth, topY + halfHeight],
          ],
          colors.top,
        );
      }
      context.shadowBlur = 0;

      if (!reducedMotion) frame = window.requestAnimationFrame(draw);
    };

    const queueDraw = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(draw);
    };

    resize();
    window.addEventListener('resize', resize);
    window.addEventListener('pointermove', onPointerMove);
    queueDraw();

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onPointerMove);
    };
  }, [open, days]);

  if (!open) return null;

  return createPortal(
    <div
      className="contribution-3d"
      role="dialog"
      aria-modal="true"
      aria-labelledby="contribution-3d-label contribution-3d-title"
      tabIndex={-1}
      onClick={onClose}
    >
      <canvas ref={canvasRef} aria-hidden="true" />
      <div className="contribution-3d__header">
        <div className="contribution-3d__title">
          <span id="contribution-3d-label">GitHub contributions</span>
          <h2 id="contribution-3d-title">{total.toLocaleString()} in the last year</h2>
        </div>
        <button
          ref={closeRef}
          className="contribution-3d__close"
          type="button"
          aria-label="Close 3D contribution view"
          onClick={(event) => {
            event.stopPropagation();
            onClose();
          }}
        >
          <Icon name="close" size={20} />
        </button>
      </div>
      <p className="contribution-3d__hint">
        <span>Move your pointer to explore</span>
        <span>click anywhere or press Esc to go back</span>
      </p>
    </div>,
    document.body,
  );
}
