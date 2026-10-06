/** Resolve only the app's loopback backend; retain the request path and query. */
export function backendProxyUrl(requestUrl: string, backendUrl: string): URL {
  const target = new URL(backendUrl);
  if (target.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(target.hostname)
      || target.username || target.password || target.pathname !== "/" || target.search || target.hash) {
    throw new Error("FORGEX_BACKEND_PROXY_TARGET_INVALID");
  }
  const incoming = new URL(requestUrl);
  const match = incoming.pathname.match(/^\/api\/(?:forgex|promptforge)(\/.*|$)/);
  if (!match) throw new Error("FORGEX_BACKEND_PROXY_PATH_INVALID");
  target.pathname = match[1] || "/";
  target.search = incoming.search;
  return target;
}
