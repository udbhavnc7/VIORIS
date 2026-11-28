import { useMemo, useState } from 'react';
import { useGitHubContributions } from '../hooks/useGitHubContributions';
import { buildContributionWeeks, formatContributionDate, getMonthLabels } from '../lib/contributions';
import { Contribution3D } from './Contribution3D';

export function ContributionHeatmap() {
  const data = useGitHubContributions();
  const [open, setOpen] = useState(false);
  const weeks = useMemo(() => buildContributionWeeks(data.contributions), [data.contributions]);
  const months = useMemo(() => getMonthLabels(weeks), [weeks]);

  return (
    <div className="contribution-widget">
      <div className="contribution-widget__header">
        <div>
          <p className="eyebrow"><span aria-hidden="true" /> Developer insights</p>
          <h2>GitHub Activity</h2>
        </div>
        <span className="contribution-widget__range">12 Months Activity</span>
      </div>

      <button
        type="button"
        className="heatmap-button"
        onClick={() => setOpen(true)}
        aria-label={`${data.total.lastYear} contributions in the last year. Open interactive 3D view.`}
      >
        <div className="heatmap">
          <div className="heatmap__months" aria-hidden="true">
            {months.map((month) => (
              <span key={`${month.label}-${month.week}`} style={{ gridColumn: month.week + 1 }}>
                {month.label}
              </span>
            ))}
          </div>
          <div
            className="heatmap__grid"
            style={{ gridTemplateColumns: `repeat(${weeks.length}, 10px)` }}
            aria-hidden="true"
          >
            {weeks.flatMap((week, weekIndex) =>
              week.map((day, dayIndex) => (
                <span
                  key={`${weekIndex}-${dayIndex}`}
                  className={`heatmap__cell heatmap__cell--level-${day?.level ?? 0}`}
                  style={{ gridColumn: weekIndex + 1, gridRow: dayIndex + 1 }}
                  title={day ? `${day.count} contributions on ${formatContributionDate(day.date)}` : undefined}
                />
              )),
            )}
          </div>
        </div>
        <div className="heatmap__footer">
          <p>{data.total.lastYear} contributions in the last year</p>
          <div className="heatmap__legend" aria-hidden="true">
            <span>Less</span>
            {[0, 1, 2, 3, 4].map((level) => (
              <i key={level} className={`heatmap__cell heatmap__cell--level-${level}`} />
            ))}
            <span>More</span>
          </div>
        </div>
      </button>

      <Contribution3D open={open} onClose={() => setOpen(false)} total={data.total.lastYear} days={data.contributions} />
    </div>
  );
}
