import { Link } from "react-router-dom";
import { useConfig } from "../config";
import { demoSettings } from "../demo";
import { promisedRetention } from "../retention";

/** One quiet line on every page of the public demo. */
export function DemoBanner() {
  const config = useConfig();
  if (demoSettings() === null) return null;
  const retention = promisedRetention(config);
  return (
    <div role="note" aria-label="Demo notice" className="border-b border-line bg-sunken text-[13px] text-ink-2">
      <p className="mx-auto max-w-6xl px-4 py-2 sm:px-6">
        <span className="mr-2 inline-flex translate-y-[-1px] items-center rounded-full border border-accent/40 px-1.5 font-mono text-[10px] font-medium tracking-wider text-accent uppercase">
          Demo
        </span>
        <strong className="font-medium text-ink">Experimental demo, not a product.</strong>{" "}
        {config?.public_repos_only && "Public repositories only. "}
        {retention && `Scans are deleted after ${retention}. `}
        Cloudflare processes this site's traffic as a data processor. To use Katz for real work, host it yourself.{" "}
        <Link to="/about" className="link text-ink">
          Details
        </Link>
      </p>
    </div>
  );
}

/** What someone should know before giving the demo read access to their repositories. */
export function BeforeYouInstall() {
  const config = useConfig();
  const demo = demoSettings();
  if (demo === null) return null;
  const retention = promisedRetention(config);
  return (
    <section aria-labelledby="before-you-install" className="card p-5 text-sm">
      <p className="eyebrow">Demo instance</p>
      <h2 id="before-you-install" className="display mt-1 text-xl">
        Before you install
      </h2>
      <ul className="mt-3 space-y-2 text-ink-2">
        <Item>This is a free experiment run by one person, with no uptime, support, or security guarantees.</Item>
        {config?.public_repos_only ? (
          <Item>It scans public repositories only. Private ones are refused, even if you install the app on them.</Item>
        ) : (
          <Item>
            Connect public repositories, or code you are allowed to share: the operator can see stored scan results,
            which include short code excerpts.
          </Item>
        )}
        {retention && (
          <Item>
            Scans are deleted automatically after {retention}, and your account details {retention} after you last sign
            in.
          </Item>
        )}
        <Item>Traffic to this site passes through Cloudflare, which processes it as a data processor.</Item>
        <Item>Reports are drafts for review by a qualified person. They are not legal advice.</Item>
        <Item>
          For private code or real assessments,{" "}
          <a className="link text-ink" href={demo.sourceUrl}>
            host Katz yourself
          </a>
          .
        </Item>
      </ul>
      <p className="mt-4 border-t border-line pt-3">
        <Link to="/about" className="link text-ink">
          What this demo stores, who else handles it, and how to have it deleted
        </Link>
      </p>
    </section>
  );
}

function Item({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-3" />
      <span>{children}</span>
    </li>
  );
}
