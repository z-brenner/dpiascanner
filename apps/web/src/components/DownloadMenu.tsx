import { api } from "../api/client";

export function DownloadMenu({ runId }: { runId: string }) {
  const formats = [
    ["md", "Markdown"],
    ["html", "HTML"],
    ["docx", "Word (DOCX)"],
    ["json", "JSON"],
  ] as const;
  return (
    <details className="relative text-sm">
      <summary className="cursor-pointer rounded border border-stone-300 px-3 py-1.5 dark:border-stone-700">Download</summary>
      <ul className="absolute right-0 z-10 mt-1 w-40 rounded border border-stone-300 bg-white p-1 shadow dark:border-stone-700 dark:bg-stone-900">
        {formats.map(([format, label]) => (
          <li key={format}>
            <a className="block rounded px-2 py-1 hover:bg-stone-100 dark:hover:bg-stone-800" href={api.reportUrl(runId, format)}>
              {label}
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}
