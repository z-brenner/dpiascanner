import type { Config } from "../api/types";
import { Notice, PageHeader } from "../components/ui";
import { useConfig } from "../config";
import { contactHref, demoSettings, repoLabel, sourceUrl, type DemoSettings } from "../demo";
import { period, promisedRetention } from "../retention";

const cell = "border-t border-line px-4 py-3 align-top";

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="display pt-2 text-2xl">{children}</h2>;
}

function Bullets({ children }: { children: React.ReactNode }) {
  return <ul className="ml-5 list-disc space-y-1.5 marker:text-ink-3">{children}</ul>;
}

export function AboutPage() {
  const demo = demoSettings();
  const config = useConfig();
  const source = sourceUrl();
  return (
    <article className="max-w-3xl space-y-10 text-[15px] leading-7 text-ink-2">
      <PageHeader eyebrow={demo ? "Demo instance" : "About"} title={demo ? "About this demo" : "About Lantern"} />
      {demo && (
        <section className="space-y-3">
          <p>
            This is an experiment, run by one person so others can try Lantern. It is not a product: there is no
            support, no uptime commitment, and it may be reset or shut down without notice.
          </p>
          <div className="card p-5">
            <p className="text-ink">
              <strong className="font-semibold">If you want to use Lantern seriously, host it yourself.</strong> The
              code is open source under the Apache 2.0 license, and you can run it on infrastructure you control, so
              your code and results never pass through anyone else.
            </p>
            <p className="mt-3 flex flex-wrap gap-x-4 text-sm">
              <a className="link text-ink" href={source}>
                Source code
              </a>
              <a className="link text-ink" href={`${source}/blob/main/docs/deploy-cloudflare.md`}>
                Deployment guide
              </a>
            </p>
          </div>
        </section>
      )}

      <section className="space-y-3">
        <H2>What Lantern does, and what it is not</H2>
        <p>
          Lantern reads a repository at one commit, traces personal data through the code, and drafts a data protection
          impact assessment under GDPR Article 35, with a CCPA/CPRA section. Every statement in a report links to the
          lines of code it rests on.
        </p>
        <p>
          Reports are drafts for review by someone qualified, such as your DPO or counsel.{" "}
          <strong className="font-semibold text-ink">They are not legal advice.</strong> The analyzer misses patterns it
          does not know yet (
          <a className="link text-ink" href={`${source}/blob/main/packages/analysis/README.md`}>
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
  const session = period(config?.session_ttl_hours ?? 168);
  const retention = promisedRetention(config);
  const configured = config?.retention && config.retention.hours > 0 ? config.retention : null;
  const jev = config?.decision_provider === "jev";
  const contact = demo.contact ? (
    <>
      contact the operator at{" "}
      <a className="link text-ink" href={contactHref(demo.contact)}>
        {demo.contact}
      </a>
    </>
  ) : (
    <>
      open an issue on{" "}
      <a className="link text-ink" href={`${demo.sourceUrl}/issues`}>
        the repository
      </a>{" "}
      that names only your GitHub login (it is public already)
    </>
  );
  return (
    <>
      <section className="space-y-3">
        <H2>Before you connect a repository</H2>
        <Bullets>
          {config?.public_repos_only ? (
            <>
              <li>
                This demo scans public repositories only. Private repositories are refused, even if you install the app on
                them.
              </li>
              <li>
                The operator can see everything this demo stores, including the short code excerpts that support each
                finding.
              </li>
            </>
          ) : (
            <>
              <li>
                Prefer public repositories. The operator can see everything this demo stores, including the short code
                excerpts that support each finding.
              </li>
              <li>Do not connect code you are not allowed to share with a third party.</li>
            </>
          )}
        </Bullets>
      </section>

      <section className="space-y-4">
        <H2>What this demo stores</H2>
        <div className="card overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="text-xs text-ink-3">
                <th className="px-4 py-2.5 font-medium">What</th>
                <th className="px-4 py-2.5 font-medium">Why</th>
                <th className="px-4 py-2.5 font-medium">For how long</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className={cell}>Your GitHub login, name, and avatar, and the installations you connect</td>
                <td className={cell}>To sign you in and list your repositories</td>
                <td className={`${cell} text-ink`}>
                  {retention ? `${retention} after you last sign in` : "Until you ask for deletion"}
                </td>
              </tr>
              <tr>
                <td className={cell}>Your GitHub access token, encrypted</td>
                <td className={cell}>To list and clone the repositories you choose</td>
                <td className={`${cell} text-ink`}>Replaced at each sign-in; deleted with your account data</td>
              </tr>
              <tr>
                <td className={cell}>A session cookie (the server keeps only a hash of it)</td>
                <td className={cell}>To keep you signed in</td>
                <td className={`${cell} text-ink`}>{session}</td>
              </tr>
              <tr>
                <td className={cell}>The repository's code</td>
                <td className={cell}>To analyze it</td>
                <td className={`${cell} text-ink`}>Only during the scan: the clone is deleted when the scan ends</td>
              </tr>
              <tr>
                <td className={cell}>Findings, reports, and short code excerpts with secrets redacted</td>
                <td className={cell}>To show you the results</td>
                <td className={`${cell} text-ink`}>{retention ? `${retention} after the scan` : "Until you ask for deletion"}</td>
              </tr>
            </tbody>
          </table>
        </div>
        {retention ? (
          <p>
            Deletion runs automatically on the server every hour, and nothing older than {retention} is ever shown.
            Uninstalling the GitHub App stops this demo from reading your repositories; results already stored are
            deleted on the schedule above, or sooner if you ask.
          </p>
        ) : configured ? (
          <Notice tone="warn" title="Automatic deletion is not running right now">
            Deletion after {period(configured.hours)} is configured, but it has not run
            {configured.last_sweep_at ? ` since ${new Date(configured.last_sweep_at).toUTCString()}` : " yet"}. Until it
            runs again, stored data stays; ask for deletion below.
          </Notice>
        ) : (
          <p>
            Uninstalling the GitHub App stops this demo from reading your repositories. It does not delete results
            already stored; see below.
          </p>
        )}
        <p>
          Deleting removes data from the live database. The host's database backups can still hold it{" "}
          {demo.backupDays ? `for up to ${period(demo.backupDays * 24)} after that` : "for a while after that"}, until they
          expire.
          {configured &&
            " If a backup is ever restored, scans past their date are still never shown, and the next sweep deletes them again."}
        </p>
      </section>

      <section className="space-y-3">
        <H2>Who else handles it</H2>
        <Bullets>
          <li>
            <strong className="font-semibold text-ink">Cloudflare</strong> serves this site and terminates its encrypted
            connections, so it processes everything sent between your browser and this demo, including session cookies
            and scan results. It acts as a data processor under its Data Processing Addendum and may process data outside
            your country, including in the United States.
          </li>
          <li>
            {demo.backendHost ? (
              <strong className="font-semibold text-ink">{demo.backendHost}</strong>
            ) : (
              "The provider of the demo's server"
            )}{" "}
            hosts the API, the scans, and the database, as a data processor.
          </li>
          <li>
            {jev ? (
              <>
                <strong className="font-semibold text-ink">TypeSafe</strong> classifies redacted, length-limited
                summaries of the data flows Lantern finds, with its Jev model.
              </>
            ) : (
              "No AI model receives your code: classification runs on this demo's server with deterministic rules."
            )}
          </li>
          <li>
            <strong className="font-semibold text-ink">GitHub</strong> already holds your account and code, and provides
            them to this demo through the app you install.
          </li>
        </Bullets>
        <p>
          If you turn on dynamic verification and this demo supports it, your repository's code is built and run in an
          isolated sandbox on the server, with no access to the internet.
        </p>
      </section>

      <section className="space-y-3">
        <H2>Deleting your data, and your rights</H2>
        <p>To have everything this demo holds about you deleted now, {contact}.</p>
        <p>
          The demo is operated by the maintainer of{" "}
          <a className="link text-ink" href={demo.sourceUrl}>
            {repoLabel(demo.sourceUrl)}
          </a>
          . It processes your data to run the scans you ask for, on the basis of the operator's legitimate interest in
          offering a free experimental tool (GDPR Art. 6(1)(f)). You can ask for access to, correction or erasure of your
          data, or object to its processing (GDPR Arts. 15 to 21), and you can complain to your data protection
          authority.
        </p>
      </section>
    </>
  );
}
