import { createContext, useContext, useEffect, useState } from "react";
import { api } from "./api/client";
import type { Config } from "./api/types";

/** undefined: no provider above; null: loading or unavailable. */
const ConfigContext = createContext<Config | null | undefined>(undefined);

/** Fetches the public /config once for everything under it. */
export function ConfigProvider({ children }: { children: React.ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null);
  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
  }, []);
  return <ConfigContext.Provider value={config}>{children}</ConfigContext.Provider>;
}

/** The server's public configuration; fetches its own copy outside a provider. */
export function useConfig(): Config | null {
  const shared = useContext(ConfigContext);
  const standalone = shared === undefined;
  const [own, setOwn] = useState<Config | null>(null);
  useEffect(() => {
    if (standalone) api.config().then(setOwn).catch(() => setOwn(null));
  }, [standalone]);
  return standalone ? own : shared;
}
