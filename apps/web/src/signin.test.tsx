import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api/client";
import type { Config } from "./api/types";
import { Layout } from "./components/Layout";
import { CallbackPage } from "./pages/CallbackPage";
import { signInHref, stateMatches } from "./signin";

const SIGNIN = "https://github.com/login/oauth/authorize?client_id=Iv1.abc&redirect_uri=https%3A%2F%2Fsite.test%2Fauth%2Fgithub%2Fcallback";

function config(overrides: Partial<Config> = {}): Config {
  return {
    install_url: "https://github.com/apps/katz-dpia/installations/new",
    signin_url: SIGNIN,
    app_slug: "katz-dpia",
    decision_provider: "stub",
    session_ttl_hours: 72,
    public_repos_only: true,
    retention: { hours: 0, active: false, last_sweep_at: null },
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("sign-in for returning users", () => {
  it("offers Sign in in the header once the visitor is known to be signed out", async () => {
    vi.spyOn(api, "me").mockRejectedValue(new ApiError(401, "not signed in"));
    vi.spyOn(api, "config").mockResolvedValue(config());
    render(
      <MemoryRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<p>home</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute("href", SIGNIN);
  });

  it("adds a state to GitHub's URL and accepts it back only once, in the same browser", () => {
    const href = new URL(signInHref(SIGNIN, "s-123"));
    expect(href.searchParams.get("state")).toBe("s-123");
    expect(href.searchParams.get("client_id")).toBe("Iv1.abc");
    sessionStorage.setItem("katz.oauth_state", "s-123");
    expect(stateMatches("s-123")).toBe(true);
    expect(stateMatches("s-123")).toBe(false); // used once
    expect(stateMatches(null)).toBe(true); // installation redirects carry no state
  });

  it("refuses a callback whose state this browser did not start", async () => {
    sessionStorage.setItem("katz.oauth_state", "mine");
    const callback = vi.spyOn(api, "callback");
    render(
      <MemoryRouter initialEntries={["/auth/github/callback?code=c0de&state=theirs"]}>
        <Routes>
          <Route path="/auth/github/callback" element={<CallbackPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/did not start in this browser/)).toBeInTheDocument();
    expect(callback).not.toHaveBeenCalled();
  });

  it("completes a sign-in whose state matches", async () => {
    sessionStorage.setItem("katz.oauth_state", "mine");
    const callback = vi.spyOn(api, "callback").mockResolvedValue(undefined as never);
    render(
      <MemoryRouter initialEntries={["/auth/github/callback?code=c0de&state=mine"]}>
        <Routes>
          <Route path="/auth/github/callback" element={<CallbackPage />} />
          <Route path="/repos" element={<p>repos</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(callback).toHaveBeenCalledWith("c0de", null, null));
    expect(await screen.findByText("repos")).toBeInTheDocument();
  });
});
