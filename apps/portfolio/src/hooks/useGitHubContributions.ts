import { useEffect, useState } from 'react';
import snapshot from '../data/contributions.json';
import type { ContributionData } from '../lib/contributions';
import { profile } from '../data/profile';

export function useGitHubContributions() {
  const [data, setData] = useState<ContributionData>(snapshot as ContributionData);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const response = await fetch(
          `https://github-contributions-api.jogruber.de/v4/${profile.githubUsername}?y=last`,
          { signal: controller.signal },
        );
        if (!response.ok) return;
        const next = (await response.json()) as ContributionData;
        if (!Array.isArray(next.contributions) || !next.total || typeof next.total.lastYear !== 'number') return;
        setData(next);
      } catch {
        // The bundled snapshot keeps the widget functional when the public API is unavailable.
      }
    };
    void load();
    return () => controller.abort();
  }, []);

  return data;
}
