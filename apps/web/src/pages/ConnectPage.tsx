import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Config, Installation } from "../api/types";
import { primaryButton } from "../components/Badge";
import { BeforeYouInstall } from "../components/DemoNotice";

export function ConnectPage() {
  const [config, setConfig] = useState<Config | null>(null);
  const [installations, setInstallations] = useState<Installation[] | null>(null);
  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
    api
      .installations()
      .then((r) => setInstallations(r.installations))
      .catch(() => setInstallations(null));
  }, []);
  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold">Connect GitHub</h1>
      <p className="text-sm">
        Lantern reads a repository at one commit, traces personal data through the code, and writes a DPIA with
        evidence. The GitHub App asks for read access to contents and metadata, and write access to checks and
        pull request comments. It cannot change your code.
      </p>
      <BeforeYouInstall />
      {config && (
        <a className={primaryButton} href={config.install_url}>
          Install the GitHub App
        </a>
      )}
      {installations && (
        <section>
          <h2 className="font-semibold">Connected installations</h2>
          {installations.length === 0 && <p className="text-sm">None yet.</p>}
          <ul className="text-sm">
            {installations.map((i) => (
              <li key={i.id}>
                {i.account} ({i.account_type.toLowerCase()}, {i.repository_selection} repositories)
                {i.suspended ? ": suspended" : ""}
              </li>
            ))}
          </ul>
          <Link className="mt-2 inline-block underline" to="/repos">
            Choose a repository
          </Link>
        </section>
      )}
    </div>
  );
}
