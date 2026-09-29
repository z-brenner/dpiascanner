import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { User } from "../api/types";
import { ConfigProvider } from "../config";
import { sourceUrl } from "../demo";
import { DemoBanner } from "./DemoNotice";
import { Mark } from "./ui";

const nav = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-2.5 py-1.5 transition-colors ${isActive ? "bg-sunken text-ink" : "text-ink-2 hover:text-ink"}`;

// The CSP allows images from this host only (and data: URIs).
const AVATAR_HOST = "https://avatars.githubusercontent.com/";

export function Layout() {
  const [user, setUser] = useState<User | null>(null);
  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null));
  }, []);
  return (
    <ConfigProvider>
      <div className="flex min-h-screen flex-col">
        <DemoBanner />
        <header className="sticky top-0 z-20 border-b border-line bg-paper/85 backdrop-blur-md">
          <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:gap-6 sm:px-6">
            <Link to="/" className="flex items-center gap-2 text-ink" aria-label="Lantern home">
              <Mark className="h-6 w-6" />
              <span className="display text-[22px] leading-none">Lantern</span>
            </Link>
            <nav className="flex items-center gap-0.5 text-sm" aria-label="Main">
              <NavLink to="/repos" className={nav}>
                Repositories
              </NavLink>
              <NavLink to="/settings" className={nav}>
                Settings
              </NavLink>
              <NavLink to="/about" className={nav}>
                About
              </NavLink>
            </nav>
            {user && (
              <div className="ml-auto flex items-center gap-2 text-sm text-ink-2">
                {user.avatar_url?.startsWith(AVATAR_HOST) && (
                  <img src={user.avatar_url} alt="" className="h-6 w-6 rounded-full border border-line" />
                )}
                <span className="hidden sm:inline">{user.login}</span>
              </div>
            )}
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-10">
          <Outlet />
        </main>
        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-5 text-xs text-ink-3 sm:px-6">
            <span className="flex items-center gap-1.5 text-ink-2">
              <Mark className="h-4 w-4" /> Lantern
            </span>
            <span>Drafts DPIAs from source code for review by a qualified person. It is not legal advice.</span>
            <span className="flex gap-3 sm:ml-auto">
              <Link to="/about" className="link">
                About
              </Link>
              <a href={sourceUrl()} className="link">
                Source
              </a>
            </span>
          </div>
        </footer>
      </div>
    </ConfigProvider>
  );
}

/** Runs an API call; sends the user to Connect on 401. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; reload: () => void } {
  const navigate = useNavigate();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    load()
      .then((d) => live && setData(d))
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 401) navigate("/", { replace: true });
        else setError(e instanceof Error ? e.message : "Request failed");
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}
