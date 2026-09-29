import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Installation } from "../api/types";
import { button, primaryButton } from "../components/Badge";
import { BeforeYouInstall } from "../components/DemoNotice";
import { useConfig } from "../config";
import { demoSettings } from "../demo";

const STEPS: [string, string, string][] = [
  [
    "Connect",
    "Install the GitHub App on the repositories you choose.",
    "It reads code at one commit. It cannot change anything.",
  ],
  [
    "Scan",
    "Lantern builds a data-flow graph of personal data.",
    "Sources, transformations, and destinations, traced deterministically, then classified.",
  ],
  [
    "Review",
    "Read a DPIA in which every claim cites its evidence.",
    "Each finding opens the path, file, and line it rests on. What cannot be resolved is flagged, never guessed.",
  ],
];

export function ConnectPage() {
  const config = useConfig();
  const [installations, setInstallations] = useState<Installation[] | null>(null);
  useEffect(() => {
    api
      .installations()
      .then((r) => setInstallations(r.installations))
      .catch(() => setInstallations(null));
  }, []);
  return (
    <div className="space-y-14">
      <section
        className={`grid gap-10 pt-2 lg:gap-14 ${demoSettings() ? "lg:grid-cols-[minmax(0,1fr)_22rem]" : ""}`}
      >
        <div className="space-y-6">
          <p className="eyebrow">Data protection impact assessments</p>
          <h1 className="display text-[2.6rem] leading-[1.05] sm:text-6xl">
            Know where personal data goes, <em className="text-ink-2">line by line.</em>
          </h1>
          <p className="max-w-xl text-[17px] leading-relaxed text-ink-2">
            Lantern reads a repository at one commit, traces personal data from where it enters to where it leaves, and
            drafts a GDPR Article 35 assessment, with a CCPA/CPRA section, in which every statement links to the code it
            rests on.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            {config && (
              <a className={primaryButton} href={config.install_url}>
                Install the GitHub App
              </a>
            )}
            {installations && installations.length > 0 ? (
              <Link className={button} to="/repos">
                Choose a repository
              </Link>
            ) : (
              <a className={button} href="#how-it-works">
                How it works
              </a>
            )}
          </div>
          <p className="max-w-xl text-xs leading-relaxed text-ink-3">
            The GitHub App asks for read access to contents and metadata, and write access to checks and pull request
            comments. It cannot change your code.
          </p>
        </div>
        <BeforeYouInstall />
      </section>

      <section id="how-it-works" aria-labelledby="how-heading" className="space-y-5">
        <h2 id="how-heading" className="eyebrow">
          How it works
        </h2>
        <ol className="grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3">
          {STEPS.map(([name, lead, detail], i) => (
            <li key={name} className="space-y-2 bg-surface p-5">
              <p className="font-mono text-xs text-ink-3">0{i + 1}</p>
              <p className="display text-xl">{name}</p>
              <p className="text-sm font-medium text-ink">{lead}</p>
              <p className="text-sm leading-relaxed text-ink-2">{detail}</p>
            </li>
          ))}
        </ol>
      </section>

      {installations && (
        <section aria-labelledby="installations-heading" className="space-y-3">
          <h2 id="installations-heading" className="eyebrow">
            Connected installations
          </h2>
          {installations.length === 0 ? (
            <p className="text-sm text-ink-2">None yet.</p>
          ) : (
            <ul className="card divide-y divide-line">
              {installations.map((i) => (
                <li key={i.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm">
                  <span className="font-medium text-ink">{i.account}</span>
                  <span className="text-ink-3">
                    {i.account_type.toLowerCase()} · {i.repository_selection} repositories
                    {i.suspended ? " · suspended" : ""}
                  </span>
                  <Link className="link ml-auto text-ink" to="/repos">
                    Choose a repository
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
