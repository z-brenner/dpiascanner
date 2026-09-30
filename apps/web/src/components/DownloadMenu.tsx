import { api } from "../api/client";
import { button } from "./Badge";

export function DownloadMenu({ runId }: { runId: string }) {
  const formats = [
    ["docx", "Word", "DOCX"],
    ["html", "Web page", "HTML"],
    ["md", "Markdown", "MD"],
    ["json", "Data", "JSON"],
  ] as const;
  return (
    <details className="group relative text-sm">
      <summary className={`${button} cursor-pointer list-none`}>
        Download
        <svg aria-hidden viewBox="0 0 12 12" className="h-3 w-3 text-ink-3 transition-transform group-open:rotate-180">
          <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </summary>
      <ul className="card absolute right-0 z-10 mt-2 w-48 p-1 shadow-lg">
        {formats.map(([format, label, ext]) => (
          <li key={format}>
            <a
              className="flex items-center justify-between rounded-md px-3 py-2 text-ink hover:bg-sunken"
              href={api.reportUrl(runId, format)}
            >
              {label}
              <span className="font-mono text-[11px] text-ink-3">{ext}</span>
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}
