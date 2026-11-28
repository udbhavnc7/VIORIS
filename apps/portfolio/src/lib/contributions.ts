export type ContributionDay = {
  date: string;
  count: number;
  level: number;
};

export type ContributionData = {
  total: { lastYear: number };
  contributions: ContributionDay[];
};

export type ContributionWeek = Array<ContributionDay | null>;

const monthLabels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function buildContributionWeeks(days: ContributionDay[]): ContributionWeek[] {
  if (!days.length) return [];
  const first = new Date(`${days[0].date}T00:00:00Z`);
  const leading = first.getUTCDay();
  const cells: Array<ContributionDay | null> = Array.from({ length: leading }, () => null);
  cells.push(...days);
  while (cells.length % 7 !== 0) cells.push(null);
  return Array.from({ length: cells.length / 7 }, (_, index) => cells.slice(index * 7, index * 7 + 7));
}

export function getMonthLabels(weeks: ContributionWeek[]) {
  const labels: Array<{ week: number; label: string }> = [];
  let previousMonth = -1;
  weeks.forEach((week, weekIndex) => {
    const firstDay = week.find((day): day is ContributionDay => Boolean(day));
    if (!firstDay) return;
    const month = new Date(`${firstDay.date}T00:00:00Z`).getUTCMonth();
    if (month !== previousMonth) {
      labels.push({ week: weekIndex, label: monthLabels[month] });
      previousMonth = month;
    }
  });
  return labels;
}

export function formatContributionDate(date: string) {
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${date}T00:00:00Z`),
  );
}
