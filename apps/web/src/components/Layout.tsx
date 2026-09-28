import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { User } from "../api/types";

const nav = ({ isActive }: { isActive: boolean }) =>
  `px-2 py-1 rounded ${isActive ? "bg-stone-200 dark:bg-stone-800" : "hover:bg-stone-100 dark:hover:bg-stone-900"}`;

export function Layout() {
  const [user, setUser] = useState<User | null>(null);
  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null));
  }, []);
  return (
    <div className="min-h-screen">
      <header className="border-b border-stone-300 dark:border-stone-800">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3 text-sm">
          <Link to="/" className="font-semibold">
            Lantern
          </Link>
          <nav className="flex gap-1">
            <NavLink to="/repos" className={nav}>
              Repositories
            </NavLink>
            <NavLink to="/settings" className={nav}>
              Settings
            </NavLink>
          </nav>
          <span className="ml-auto text-stone-600 dark:text-stone-400">{user ? user.login : ""}</span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
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
