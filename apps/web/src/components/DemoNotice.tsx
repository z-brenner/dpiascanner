import { Link } from "react-router-dom";
import { demoSettings } from "../demo";

const tone =
  "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100";

/** One line on every page of the public demo. */
export function DemoBanner() {
  if (demoSettings() === null) return null;
  return (
    <div role="note" aria-label="Demo notice" className={`border-b text-sm ${tone}`}>
      <p className="mx-auto max-w-6xl px-4 py-2">
        <strong className="font-semibold">Experimental demo, not a product.</strong> Cloudflare processes this site's
        traffic as a data processor. To use Lantern for real work, host it yourself.{" "}
        <Link to="/about" className="underline">
          Details
        </Link>
      </p>
    </div>
  );
}

/** What someone should know before giving the demo read access to their repositories. */
export function BeforeYouInstall() {
  const demo = demoSettings();
  if (demo === null) return null;
  return (
    <section aria-labelledby="before-you-install" className={`rounded border p-4 text-sm ${tone}`}>
      <h2 id="before-you-install" className="font-semibold">
        Before you install
      </h2>
      <ul className="mt-2 list-disc space-y-1 pl-5">
        <li>This is a free experiment run by one person, with no uptime, support, or security guarantees.</li>
        <li>
          Connect public repositories, or code you are allowed to share: the operator can see stored scan results,
          which include short code excerpts.
        </li>
        <li>Traffic to this site passes through Cloudflare, which processes it as a data processor.</li>
        <li>Reports are drafts for review by a qualified person. They are not legal advice.</li>
        <li>
          For private code or real assessments,{" "}
          <a className="underline" href={demo.sourceUrl}>
            host Lantern yourself
          </a>
          .
        </li>
      </ul>
      <p className="mt-2">
        <Link to="/about" className="underline">
          What this demo stores, who else handles it, and how to have it deleted
        </Link>
      </p>
    </section>
  );
}
