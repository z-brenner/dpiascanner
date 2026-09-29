/**
 * Cloudflare Worker for the Lantern web app.
 *
 * Cloudflare serves the Vite build (dist/) directly, falling back to index.html for client
 * routes; see wrangler.jsonc. Only /api/* runs this code. It forwards the request to the API
 * origin with the /api prefix removed, as the Vite dev server does, so the browser talks to a
 * single origin and the API's session cookie stays first-party (HttpOnly, SameSite=Lax).
 *
 * This module may only export the handler: workerd treats named exports as entrypoints.
 */

import { isApiPath, proxy, type Env } from "./proxy";

export default {
  async fetch(request, env): Promise<Response> {
    if (isApiPath(new URL(request.url).pathname)) return proxy(request, env);
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;
