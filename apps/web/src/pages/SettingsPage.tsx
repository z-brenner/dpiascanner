import { api } from "../api/client";
import { useLoad } from "../components/Layout";

export function SettingsPage() {
  const { data, error } = useLoad(() => api.settings(), []);
  if (error) return <p className="text-red-700">{error}</p>;
  if (!data) return <p className="text-sm">Loading…</p>;
  const rows: [string, string][] = [
    ["Confidence threshold", `${data.threshold} (decisions below it are reported as unresolved)`],
    ["Question set", `${data.question_set_version ?? "–"} (available: ${data.question_set_versions.join(", ")})`],
    ["SDK registry", data.registry_version],
    ["Decision provider", data.decision_provider],
    ["Severity table", data.severity_version],
    ["Statute map", data.statutes_version],
    ["Runs per hour per user", String(data.runs_per_hour)],
    ["Run timeout", `${Math.round(data.run_timeout_s / 60)} minutes`],
    ["Clone size cap", `${data.clone_max_mb} MB`],
  ];
  return (
    <div className="max-w-2xl space-y-3">
      <h1 className="text-xl font-semibold">Settings</h1>
      <p className="text-sm text-stone-600 dark:text-stone-400">
        These are set on the server. Every finding records the question set, provider, and commit it came from.
      </p>
      <dl className="divide-y divide-stone-200 text-sm dark:divide-stone-800">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-3 gap-2 py-2">
            <dt className="font-medium">{k}</dt>
            <dd className="col-span-2">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
