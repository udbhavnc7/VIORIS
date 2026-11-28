import { useEffect, useState } from 'react';
import { profile } from '../data/profile';

export type GitHubProfileStats = {
  followers: number;
  stars: number;
  repos: number;
  forks: number;
};

const snapshot: GitHubProfileStats = { followers: 2, stars: 0, repos: 15, forks: 1 };

type GitHubUser = { public_repos: number; followers: number };
type GitHubRepo = { stargazers_count: number; forks_count: number };

export function useGitHubProfile() {
  const [stats, setStats] = useState<GitHubProfileStats>(snapshot);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const [userResponse, reposResponse] = await Promise.all([
          fetch(`https://api.github.com/users/${profile.githubUsername}`, {
            signal: controller.signal,
            headers: { Accept: 'application/vnd.github+json' },
          }),
          fetch(`https://api.github.com/users/${profile.githubUsername}/repos?per_page=100`, {
            signal: controller.signal,
            headers: { Accept: 'application/vnd.github+json' },
          }),
        ]);
        if (!userResponse.ok || !reposResponse.ok) return;
        const user = (await userResponse.json()) as GitHubUser;
        const repos = (await reposResponse.json()) as GitHubRepo[];
        setStats({
          followers: user.followers,
          repos: user.public_repos,
          stars: repos.reduce((total, repo) => total + repo.stargazers_count, 0),
          forks: repos.reduce((total, repo) => total + repo.forks_count, 0),
        });
      } catch {
        // Keep the verified snapshot if GitHub's public API is unavailable or rate-limited.
      }
    };
    void load();
    return () => controller.abort();
  }, []);

  return stats;
}
