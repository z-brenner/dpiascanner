import { useState } from "react";
import type { RunOptions } from "../api/types";
import { button, input, primaryButton } from "./Badge";

interface Props {
  repo: string;
  defaultBranch: string;
  busy?: boolean;
  onSubmit: (ref: string, options: RunOptions) => void;
  onCancel: () => void;
}

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
      className="space-y-3 rounded border border-stone-300 bg-white p-4 text-sm dark:border-stone-700 dark:bg-stone-900"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(ref.trim() || defaultBranch, { languages, dynamic, dependency_depth: depth });
      }}
    >
      <p className="font-medium">
        Scan <span className="font-mono">{repo}</span>
      </p>
      <label className="flex flex-col gap-1">
        Branch, tag, or commit
        <input className={input} value={ref} onChange={(e) => setRef(e.target.value)} />
      </label>
      <fieldset>
        <legend>Languages (none selected: detect)</legend>
        {(["python", "typescript"] as const).map((l) => (
          <label key={l} className="mr-4 inline-flex items-center gap-1">
            <input type="checkbox" checked={languages?.includes(l) ?? false} onChange={() => toggle(l)} /> {l}
          </label>
        ))}
      </fieldset>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={dynamic} onChange={(e) => setDynamic(e.target.checked)} />
        Dynamic verification (runs the code in a network-isolated sandbox with synthetic data)
      </label>
      <label className="flex flex-col gap-1">
        Dependency depth
        <select className={input} value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
          <option value={0}>0: registry SDKs only</option>
          <option value={1}>1: also analyze unregistered dependencies one level deep</option>
        </select>
      </label>
      <div className="flex gap-2">
        <button type="submit" className={primaryButton} disabled={busy}>
          {busy ? "Starting…" : "Start run"}
        </button>
        <button type="button" className={button} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
