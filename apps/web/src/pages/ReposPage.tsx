import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Repo, RunOptions } from "../api/types";
import { Pill, button, input } from "../components/Badge";
import { RepoResolverField } from "../components/RepoResolverField";
import { RunOptionsPanel } from "../components/RunOptionsPanel";
import { Notice, PageHeader, SectionTitle } from "../components/ui";
import { useConfig } from "../config";

interface Target {
  owner: string;
  repo: string;
  defaultBranch: string;
}

export function ReposPage() {
  const navigate = useNavigate();
  const config = useConfig();
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
    <div className="space-y-10">
      <PageHeader eyebrow="Scan" title="Repositories">
        Paste any GitHub URL, or pick one of the repositories you connected.
        {config?.public_repos_only && " This instance scans public repositories only."}
      </PageHeader>

      <section className="space-y-3">
        <SectionTitle>Check a repository</SectionTitle>
        <RepoResolverField
          resolve={api.resolve}
          onRun={(r) =>
            r.owner && r.repo && setTarget({ owner: r.owner, repo: r.repo, defaultBranch: r.default_branch ?? "main" })
          }
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
      {error && <Notice tone="bad">{error}</Notice>}

      <section className="space-y-3">
        <SectionTitle aside={`${total} ${total === 1 ? "repository" : "repositories"}`}>Your repositories</SectionTitle>
        <label className="sr-only" htmlFor="repo-search">
          Search repositories
        </label>
        <input
          id="repo-search"
          className={input}
          placeholder="Search by name"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {repos.length > 0 && (
          <ul className="card divide-y divide-line">
            {repos.map((r) => {
              const [owner, name] = r.full_name.split("/") as [string, string];
              const scannable = r.scannable !== false;
              return (
                <li key={r.full_name} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 text-sm">
                  <span className="font-mono text-[13px]">
                    <span className="text-ink-3">{owner}/</span>
                    <span className="font-medium text-ink">{name}</span>
                  </span>
                  {r.private && <Pill tone={scannable ? "neutral" : "warn"}>{scannable ? "private" : "private: not scanned here"}</Pill>}
                  <span className="ml-auto flex items-center gap-3">
                    <Link className="link text-ink-2" to={`/repos/${owner}/${name}/diff`}>
                      Runs and diffs
                    </Link>
                    <button
                      type="button"
                      className={button}
                      disabled={!scannable}
                      title={scannable ? undefined : "This instance scans public repositories only"}
                      onClick={() => setTarget({ owner, repo: name, defaultBranch: r.default_branch })}
                    >
                      Run
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
