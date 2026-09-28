import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Repo, RunOptions } from "../api/types";
import { Pill, button, input } from "../components/Badge";
import { RepoResolverField } from "../components/RepoResolverField";
import { RunOptionsPanel } from "../components/RunOptionsPanel";

interface Target {
  owner: string;
  repo: string;
  defaultBranch: string;
}

export function ReposPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [repos, setRepos] = useState<Repo[]>([]);
  const [total, setTotal] = useState(0);
  const [target, setTarget] = useState<Target | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .repos(query)
        .then((page) => {
          setRepos(page.items);
          setTotal(page.total);
        })
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 401) navigate("/", { replace: true });
          else setError(e instanceof Error ? e.message : "Could not list repositories");
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [query, navigate]);

  async function start(ref: string, options: RunOptions) {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun(target.owner, target.repo, ref, options);
      navigate(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the run");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h1 className="text-xl font-semibold">Scan a repository</h1>
        <RepoResolverField
          resolve={api.resolve}
          onRun={(r) => r.owner && r.repo && setTarget({ owner: r.owner, repo: r.repo, defaultBranch: r.default_branch ?? "main" })}
        />
      </section>
      {target && (
        <RunOptionsPanel
          repo={`${target.owner}/${target.repo}`}
          defaultBranch={target.defaultBranch}
          busy={busy}
          onSubmit={(ref, options) => void start(ref, options)}
          onCancel={() => setTarget(null)}
        />
      )}
      {error && <p className="text-sm text-red-700 dark:text-red-300">{error}</p>}
      <section className="space-y-2">
        <h2 className="font-semibold">Your repositories</h2>
        <label className="sr-only" htmlFor="repo-search">
          Search repositories
        </label>
        <input id="repo-search" className={input} placeholder="Search by name" value={query} onChange={(e) => setQuery(e.target.value)} />
        <p className="text-xs text-stone-500">{total} repositories</p>
        <ul className="divide-y divide-stone-200 dark:divide-stone-800">
          {repos.map((r) => {
            const [owner, name] = r.full_name.split("/") as [string, string];
            return (
              <li key={r.full_name} className="flex items-center gap-3 py-2 text-sm">
                <span className="font-mono">{r.full_name}</span>
                {r.private && <Pill>private</Pill>}
                <Link className="ml-auto underline" to={`/repos/${owner}/${name}/diff`}>
                  Runs and diffs
                </Link>
                <button type="button" className={button} onClick={() => setTarget({ owner, repo: name, defaultBranch: r.default_branch })}>
                  Run
                </button>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
