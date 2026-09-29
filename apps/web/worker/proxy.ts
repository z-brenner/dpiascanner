/**
 * The /api reverse proxy used by the Worker (index.ts). Kept out of the entry module because
 * workerd treats every named export of that module as an entrypoint.
 *
 * Nothing here reads, logs, or stores request bodies or cookies; they pass through untouched.
 */

export interface Env {
  ASSETS: Fetcher;
  /** Origin of the FastAPI service, such as https://api.lantern.example.com. */
  API_ORIGIN?: string;
  /** Cloudflare Access service token, when the API hostname sits behind Access. */
  CF_ACCESS_CLIENT_ID?: string;
  CF_ACCESS_CLIENT_SECRET?: string;
}

export const API_PREFIX = "/api";

// Hop-by-hop headers (RFC 9110, section 7.6.1) and the headers this proxy sets itself, which a
// client must not be able to supply. Cloudflare's own cf-* headers are dropped too: the client
// IP travels as X-Forwarded-For, and an Access token only ever comes from this Worker.
const DROPPED_REQUEST_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-proto",
]);

export function isApiPath(pathname: string): boolean {
  return pathname === API_PREFIX || pathname.startsWith(`${API_PREFIX}/`);
}

/**
 * The API origin from configuration: https only, except plain http to this machine for
 * `wrangler dev` against a local API. Anything else is treated as not configured.
 */
export function apiOrigin(value: string | undefined): URL | null {
  if (!value) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) return null;
  if (url.username || url.password) return null;
  return url;
}

/**
 * The upstream URL for an /api request. The host always comes from the configured origin:
 * the client's path is only ever assigned as a path, so `/api//evil.example/x` stays on the
 * API host.
 */
export function upstreamUrl(request: URL, origin: URL): URL {
  const target = new URL(origin.origin);
  const basePath = origin.pathname.replace(/\/+$/, "");
  target.pathname = `${basePath}${request.pathname.slice(API_PREFIX.length) || "/"}`;
  target.search = request.search;
  return target;
}

export function forwardHeaders(request: Request, env: Env): Headers {
  const out = new Headers();
  request.headers.forEach((value, name) => {
    const key = name.toLowerCase();
    if (DROPPED_REQUEST_HEADERS.has(key) || key.startsWith("cf-")) return;
    out.set(name, value);
  });
  const url = new URL(request.url);
  out.set("X-Forwarded-Host", url.host);
  out.set("X-Forwarded-Proto", url.protocol.replace(/:$/, ""));
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) out.set("X-Forwarded-For", ip);
  if (env.CF_ACCESS_CLIENT_ID && env.CF_ACCESS_CLIENT_SECRET) {
    out.set("CF-Access-Client-Id", env.CF_ACCESS_CLIENT_ID);
    out.set("CF-Access-Client-Secret", env.CF_ACCESS_CLIENT_SECRET);
  }
  return out;
}

/**
 * Redirects the API issues point at its own host (FastAPI's trailing-slash redirect, for
 * one). Send the browser back through this proxy instead. A relative Location resolves
 * against the upstream URL that was requested.
 */
export function rewriteLocation(location: string, origin: URL, site: URL, requested?: URL): string {
  let url: URL;
  try {
    url = new URL(location, requested ?? origin);
  } catch {
    return location;
  }
  if (url.origin !== origin.origin) return location;
  const basePath = origin.pathname.replace(/\/+$/, "");
  const underBase = url.pathname === basePath || url.pathname.startsWith(`${basePath}/`);
  const path = underBase ? url.pathname.slice(basePath.length) : url.pathname;
  return `${site.origin}${API_PREFIX}${path || "/"}${url.search}${url.hash}`;
}

function problem(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export async function proxy(request: Request, env: Env): Promise<Response> {
  const origin = apiOrigin(env.API_ORIGIN);
  if (origin === null) {
    return problem(503, "The API is not configured for this site (API_ORIGIN).");
  }
  const site = new URL(request.url);
  const target = upstreamUrl(site, origin);
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: forwardHeaders(request, env),
      // Buffered: request bodies here are small JSON documents and webhook payloads, and a
      // buffer forwards the exact bytes GitHub signed.
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
    });
  } catch {
    return problem(502, "The API did not respond.");
  }
  const response = new Response(upstream.body, upstream);
  const location = response.headers.get("Location");
  if (location) response.headers.set("Location", rewriteLocation(location, origin, site, target));
  if (!response.headers.has("Cache-Control")) response.headers.set("Cache-Control", "no-store");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
