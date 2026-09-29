import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Config } from "../api/types";
import { contactHref, demoSettings, repoLabel, sourceUrl, type DemoSettings } from "../demo";

const cell = "border-t border-stone-300 px-2 py-1.5 align-top dark:border-stone-800";

export function AboutPage() {
  const demo = demoSettings();
  const [config, setConfig] = useState<Config | null>(null);
  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
  }, []);
  const source = sourceUrl();
  return (
    <article className="max-w-3xl space-y-6 text-sm leading-relaxed">
      <h1 className="text-xl font-semibold">{demo ? "About this demo" : "About Lantern"}</h1>
      {demo && (
        <section className="space-y-2">
          <p>
            This is an experiment, run by one person so others can try Lantern. It is not a product: there is no
            support, no uptime commitment, and it may be reset or shut down without notice.
          </p>
          <p>
            <strong className="font-semibold">If you want to use Lantern seriously, host it yourself.</strong> The
            code is open source under the Apache 2.0 license, and you can run it on infrastructure you control, so
            your code and results never pass through anyone else.{" "}
            <a className="underline" href={source}>
              Source code
            </a>{" "}
            ·{" "}
            <a className="underline" href={`${source}/blob/main/docs/deploy-cloudflare.md`}>
              Deployment guide
            </a>
          </p>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-base font-semibold">What Lantern does, and what it is not</h2>
        <p>
          Lantern reads a repository at one commit, traces personal data through the code, and drafts a data
          protection impact assessment under GDPR Article 35, with a CCPA/CPRA section. Every statement in a report
          links to the lines of code it rests on.
        </p>
        <p>
          Reports are drafts for review by someone qualified, such as your DPO or counsel.{" "}
          <strong className="font-semibold">They are not legal advice.</strong> The analyzer misses patterns it does
          not know yet (
          <a className="underline" href={`${source}/blob/main/packages/analysis/README.md`}>
            known gaps
          </a>
          ), and what it cannot resolve is listed as unresolved rather than guessed.
        </p>
      </section>

      {demo && <DemoDataNotice demo={demo} config={config} />}
    </article>
  );
}

function DemoDataNotice({ demo, config }: { demo: DemoSettings; config: Config | null }) {
  const sessionDays = config ? Math.round(config.session_ttl_hours / 24) : 7;
  const jev = config?.decision_provider === "jev";
  const contact = demo.contact ? (
    <>
      contact the operator at{" "}
      <a className="underline" href={contactHref(demo.contact)}>
        {demo.contact}
      </a>
    </>
  ) : (
    <>
      open an issue on{" "}
      <a className="underline" href={`${demo.sourceUrl}/issues`}>
        the repository
      </a>{" "}
      that names only your GitHub login (it is public already)
    </>
  );
  return (
    <>
      <section className="space-y-2">
        <h2 className="text-base font-semibold">Before you connect a repository</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            Prefer public repositories. The operator can see everything this demo stores, including the short code
            excerpts that support each finding.
          </li>
          <li>Do not connect code you are not allowed to share with a third party.</li>
        </ul>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">What this demo stores</h2>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr>
                <th className="px-2 py-1.5 font-semibold">What</th>
                <th className="px-2 py-1.5 font-semibold">Why</th>
                <th className="px-2 py-1.5 font-semibold">For how long</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className={cell}>Your GitHub login, name, and avatar, and the installations you connect</td>
                <td className={cell}>To sign you in and list your repositories</td>
                <td className={cell}>Until you ask for deletion</td>
              </tr>
              <tr>
                <td className={cell}>Your GitHub access token, encrypted</td>
                <td className={cell}>To list and clone the repositories you choose</td>
                <td className={cell}>Replaced at each sign-in; deleted with your account data</td>
              </tr>
              <tr>
                <td className={cell}>A session cookie (the server keeps only a hash of it)</td>
                <td className={cell}>To keep you signed in</td>
                <td className={cell}>{sessionDays} days</td>
              </tr>
              <tr>
                <td className={cell}>The repository's code</td>
                <td className={cell}>To analyze it</td>
                <td className={cell}>Only during the scan: the clone is deleted when the scan ends</td>
              </tr>
              <tr>
                <td className={cell}>Findings, reports, and short code excerpts with secrets redacted</td>
                <td className={cell}>To show you the results</td>
                <td className={cell}>Until you ask for deletion</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p>
          Uninstalling the GitHub App stops this demo from reading your repositories. It does not delete results
          already stored; see below.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Who else handles it</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <strong className="font-semibold">Cloudflare</strong> serves this site and terminates its encrypted
            connections, so it processes everything sent between your browser and this demo, including session
            cookies and scan results. It acts as a data processor under its Data Processing Addendum and may process
            data outside your country, including in the United States.
          </li>
          <li>
            {demo.backendHost ? (
              <strong className="font-semibold">{demo.backendHost}</strong>
            ) : (
              "The provider of the demo's server"
            )}{" "}
            hosts the API, the scans, and the database, as a data processor.
          </li>
          <li>
            {jev ? (
              <>
                <strong className="font-semibold">TypeSafe</strong> classifies redacted, length-limited summaries of
                the data flows Lantern finds, with its Jev model.
              </>
            ) : (
              "No AI model receives your code: classification runs on this demo's server with deterministic rules."
            )}
          </li>
          <li>
            <strong className="font-semibold">GitHub</strong> already holds your account and code, and provides them
            to this demo through the app you install.
          </li>
        </ul>
        <p>
          If you turn on dynamic verification and this demo supports it, your repository's code is built and run in
          an isolated sandbox on the server, with no access to the internet.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Deleting your data, and your rights</h2>
        <p>To have everything this demo holds about you deleted, {contact}.</p>
        <p>
          The demo is operated by the maintainer of{" "}
          <a className="underline" href={demo.sourceUrl}>
            {repoLabel(demo.sourceUrl)}
          </a>
          . It processes your data to run the scans you ask for, on the basis of the operator's legitimate interest in
          offering a free experimental tool (GDPR Art. 6(1)(f)). You can ask for access to, correction or erasure of
          your data, or object to its processing (GDPR Arts. 15 to 21), and you can complain to your data protection
          authority.
        </p>
      </section>
    </>
  );
}
