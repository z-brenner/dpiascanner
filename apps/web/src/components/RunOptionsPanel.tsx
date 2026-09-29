import { useState } from "react";
import type { RunOptions } from "../api/types";
import { ghostButton, input, primaryButton } from "./Badge";

interface Props {
  repo: string;
  defaultBranch: string;
  busy?: boolean;
  onSubmit: (ref: string, options: RunOptions) => void;
  onCancel: () => void;
}

const LANGUAGES = [
  ["python", "Python"],
  ["typescript", "TypeScript"],
] as const;

export function RunOptionsPanel({ repo, defaultBranch, busy, onSubmit, onCancel }: Props) {
  const [ref, setRef] = useState(defaultBranch);
  const [languages, setLanguages] = useState<RunOptions["languages"]>(null);
  const [dynamic, setDynamic] = useState(false);
  const [depth, setDepth] = useState(0);

  function toggle(language: "python" | "typescript") {
    const current = languages ?? [];
    const next = current.includes(language) ? current.filter((l) => l !== language) : [...current, language];
    setLanguages(next.length ? next : null);
  }

  return (
    <form
      aria-label={`Run options for ${repo}`}
      className="card space-y-5 p-5 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(ref.trim() || defaultBranch, { languages, dynamic, dependency_depth: depth });
      }}
    >
      <div>
        <p className="eyebrow">New scan</p>
        <p className="mt-1 font-mono text-base text-ink">{repo}</p>
      </div>
      <div className="grid gap-5 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className="font-medium text-ink">Branch, tag, or commit</span>
          <input className={`${input} font-mono text-[13px]`} value={ref} onChange={(e) => setRef(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="font-medium text-ink">Dependency depth</span>
          <select className={input} value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            <option value={0}>0: registry SDKs only</option>
            <option value={1}>1: also analyze unregistered dependencies one level deep</option>
          </select>
        </label>
      </div>
      <fieldset className="space-y-2">
        <legend className="font-medium text-ink">Languages</legend>
        <div className="flex flex-wrap items-center gap-2">
          {LANGUAGES.map(([id, name]) => {
            const on = languages?.includes(id) ?? false;
            return (
              <label
                key={id}
                className={`inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 transition-colors ${
                  on ? "border-ink bg-ink text-paper" : "border-line-strong bg-surface text-ink-2 hover:text-ink"
                }`}
              >
                <input type="checkbox" className="sr-only" checked={on} onChange={() => toggle(id)} />
                {name}
              </label>
            );
          })}
          <span className="text-xs text-ink-3">None selected: detect from the repository.</span>
        </div>
      </fieldset>
      <label className="flex items-start gap-3 rounded-lg bg-sunken p-3">
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 accent-[var(--ink)]"
          checked={dynamic}
          onChange={(e) => setDynamic(e.target.checked)}
        />
        <span>
          <span className="font-medium text-ink">Dynamic verification</span>
          <span className="block text-ink-2">
            Runs the code in a network-isolated sandbox with synthetic data, where this instance supports it.
          </span>
        </span>
      </label>
      <div className="flex items-center gap-2 border-t border-line pt-4">
        <button type="submit" className={primaryButton} disabled={busy}>
          {busy ? "Starting…" : "Start run"}
        </button>
        <button type="button" className={ghostButton} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
