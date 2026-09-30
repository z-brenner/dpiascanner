/**
 * Sign-in for people who installed the GitHub App before. GitHub's authorization page sends
 * them back to the installation callback; a random state value ties that redirect to this
 * browser, so a callback link made elsewhere cannot sign someone into another account.
 */

const STATE_KEY = "katz.oauth_state";

export function signInHref(url: string, state: string): string {
  const target = new URL(url);
  target.searchParams.set("state", state);
  return target.toString();
}

export function startSignIn(url: string): void {
  const state = crypto.randomUUID();
  try {
    sessionStorage.setItem(STATE_KEY, state);
  } catch {
    // Without storage the callback cannot check the state and refuses it; nothing else breaks.
  }
  window.location.assign(signInHref(url, state));
}

/**
 * Whether the callback may continue. Installation redirects carry no state; a sign-in's state
 * must match the one this browser stored, and is used once.
 */
export function stateMatches(returned: string | null): boolean {
  if (returned === null) return true;
  let expected: string | null = null;
  try {
    expected = sessionStorage.getItem(STATE_KEY);
    sessionStorage.removeItem(STATE_KEY);
  } catch {
    return false;
  }
  return expected !== null && expected === returned;
}

export function SignInLink({ url, className, children }: { url: string; className?: string; children: React.ReactNode }) {
  return (
    <a
      href={url}
      className={className}
      onClick={(event) => {
        event.preventDefault();
        startSignIn(url);
      }}
    >
      {children}
    </a>
  );
}
