/**
 * The UI host's only network path: a narrow, allowlisted proxy to the local
 * Python gateway. The browser never talks to the task runner, connectors, or
 * any other service directly, and this process holds no credentials.
 */

const ALLOWED_EXACT = new Set([
  "/health",
  "/v1",
  "/devices",
  "/auth/device/pair",
  "/auth/device/exchange",
]);

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export function isAllowedPath(pathname) {
  if (!pathname) return false;
  // Refuse traversal outright rather than relying on what the gateway does
  // with a normalised path.
  if (pathname.includes("..") || pathname.includes("\\")) return false;
  if (ALLOWED_EXACT.has(pathname)) return true;
  if (pathname.startsWith("/v1/")) return true;
  if (/^\/devices\/[^/]+\/revoke$/.test(pathname)) return true;
  return false;
}

function hostnameOf(hostHeader) {
  if (!hostHeader) return "";
  const withoutPort = hostHeader.replace(/:\d+$/, "");
  return withoutPort.toLowerCase();
}

export function installSecurity(app, { extraHosts = [], dev = false } = {}) {
  const allowed = new Set([...LOCAL_HOSTNAMES, ...extraHosts.map((h) => h.toLowerCase())]);
  // Vite injects the React refresh preamble inline in dev and talks to an HMR
  // socket on a sibling port. Production keeps the strict policy below.
  const scriptSrc = dev ? "script-src 'self' 'unsafe-inline'" : "script-src 'self'";
  const connectSrc = dev
    ? "connect-src 'self' ws://127.0.0.1:* ws://localhost:* ws://[::1]:*"
    : "connect-src 'self'";

  app.disable("x-powered-by");
  app.use((req, res, next) => {
    // DNS-rebinding guard: even though we bind loopback, a hostile page can
    // point its own hostname at 127.0.0.1. Only local hostnames are accepted.
    const host = hostnameOf(req.headers.host);
    if (!allowed.has(host)) {
      res.status(403).json({ detail: "This UI only answers on a local hostname." });
      return;
    }
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("X-Frame-Options", "DENY");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
    res.setHeader(
      "Content-Security-Policy",
      [
        "default-src 'self'",
        scriptSrc,
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        connectSrc,
        "media-src 'self' blob:",
        "worker-src 'self' blob:",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
      ].join("; "),
    );
    next();
  });
}

export function installProxy(app, { gatewayUrl }) {
  const gateway = gatewayUrl.replace(/\/$/, "");

  app.use(async (req, res, next) => {
    if (!isAllowedPath(req.path)) return next();

    // `/devices` is both an API call and a browser route. A deep link or a
    // refresh arrives without a device token; hand it to the SPA instead of
    // forwarding a request that can only fail with 401 JSON in the tab.
    if (req.method === "GET" && req.path === "/devices" && !req.headers.authorization) {
      return next();
    }

    const headers = {};
    if (req.headers.authorization) headers.authorization = req.headers.authorization;
    if (req.headers["content-type"]) headers["content-type"] = req.headers["content-type"];
    if (req.headers["x-vioris-second-factor"]) {
      headers["x-vioris-second-factor"] = req.headers["x-vioris-second-factor"];
    }

    const hasBody = ["POST", "PUT", "PATCH", "DELETE"].includes(req.method);
    const body = hasBody && req.body !== undefined ? JSON.stringify(req.body) : undefined;

    try {
      const upstream = await fetch(`${gateway}${req.originalUrl}`, {
        method: req.method,
        headers,
        body,
        redirect: "manual",
        signal: AbortSignal.timeout(30_000),
      });
      const payload = Buffer.from(await upstream.arrayBuffer());
      const contentType = upstream.headers.get("content-type");
      if (contentType) res.setHeader("content-type", contentType);
      res.setHeader("cache-control", "no-store");
      res.status(upstream.status).send(payload);
    } catch (error) {
      const reason = error?.name === "TimeoutError" ? "timed out" : error?.message || "unreachable";
      res.status(502).json({
        detail: `The local gateway is ${reason}. Start it with: python run_vioris.py`,
      });
    }
  });
}
