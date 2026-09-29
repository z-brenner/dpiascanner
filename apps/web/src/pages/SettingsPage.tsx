import { api } from "../api/client";
import { useLoad } from "../components/Layout";
import { ErrorText, Loading, PageHeader } from "../components/ui";

export function SettingsPage() {
  const { data, error } = useLoad(() => api.settings(), []);
  if (error) return <ErrorText>{error}</ErrorText>;
  if (!data) return <Loading what="Loading settings" />;
  const rows: [string, React.ReactNode, string?][] = [
    ["Confidence threshold", data.threshold, "Decisions below it are reported as unresolved."],
    ["Question set", data.question_set_version ?? "–", `Available: ${data.question_set_versions.join(", ")}`],
    ["SDK registry", data.registry_version],
    ["Decision provider", data.decision_provider],
    ["Severity table", data.severity_version],
    ["Statute map", data.statutes_version],
    ["Runs per hour per user", data.runs_per_hour],
    ["Run timeout", `${Math.round(data.run_timeout_s / 60)} minutes`],
    ["Clone size cap", `${data.clone_max_mb} MB`],
  ];
  return (
    <div className="max-w-3xl space-y-8">
      <PageHeader eyebrow="Server" title="Settings">
        These are set on the server. Every finding records the question set, provider, and commit it came from.
      </PageHeader>
      <dl className="card divide-y divide-line">
        {rows.map(([k, v, note]) => (
          <div key={k} className="grid gap-1 px-5 py-3.5 text-sm sm:grid-cols-[14rem_1fr] sm:gap-4">
            <dt className="text-ink-2">{k}</dt>
            <dd>
              <span className="font-mono text-[13px] text-ink">{v}</span>
              {note && <span className="block text-xs text-ink-3">{note}</span>}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
