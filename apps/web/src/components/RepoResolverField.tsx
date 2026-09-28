import { useState } from "react";
import type { Resolution } from "../api/types";
import { Pill, button, input, primaryButton } from "./Badge";

const STATUS_LABEL: Record<Resolution["status"], { text: string; tone: "good" | "warn" | "bad" }> = {
  installed: { text: "Can be scanned", tone: "good" },
  public: { text: "Public: can be scanned", tone: "good" },
  inaccessible: { text: "Cannot be scanned yet", tone: "warn" },
  invalid: { text: "Not a GitHub repository", tone: "bad" },
};

interface Props {
  resolve: (input: string) => Promise<Resolution>;
  onRun: (resolution: Resolution) => void;
}

/** Paste or type any GitHub URL or owner/repo; shows whether it can be scanned. */
export function RepoResolverField({ resolve, onRun }: Props) {
  const [value, setValue] = useState("");
  const [result, setResult] = useState<Resolution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function check(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await resolve(trimmed));
    } catch (exc) {
      setResult(null);
      setError(exc instanceof Error ? exc.message : "Could not check the repository.");
    } finally {
      setBusy(false);
    }
  }

  const label = result ? STATUS_LABEL[result.status] : null;
  return (
    <div className="space-y-2">
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void check(value);
        }}
      >
        <label className="sr-only" htmlFor="repo-resolver">
          Repository URL or owner/repo
        </label>
        <input
          id="repo-resolver"
          className={input}
          placeholder="https://github.com/owner/repo, git@github.com:owner/repo.git, or owner/repo"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            setResult(null);
          }}
          onPaste={(event) => {
            const pasted = event.clipboardData.getData("text");
            if (pasted) {
              event.preventDefault();
              setValue(pasted.trim());
              void check(pasted);
            }
          }}
        />
        <button type="submit" className={button} disabled={busy || !value.trim()}>
          {busy ? "Checking…" : "Check"}
        </button>
      </form>
      <div role="status" aria-live="polite" className="min-h-6 text-sm">
        {error && <p className="text-red-700 dark:text-red-300">{error}</p>}
        {result && label && (
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone={label.tone}>{label.text}</Pill>
            {result.full_name && <span className="font-mono">{result.full_name}</span>}
            {result.reason && <span className="text-stone-600 dark:text-stone-400">{result.reason}</span>}
            {result.scannable && (
              <button type="button" className={primaryButton} onClick={() => onRun(result)}>
                Run
              </button>
            )}
            {!result.scannable && result.install_url && (
              <a className={button} href={result.install_url} target="_blank" rel="noreferrer">
                Install the Lantern app
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
