import express from "express";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { installProxy, installSecurity } from "./proxy.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.resolve(here, "../dist");

const PORT = Number(process.env.VIORIS_UI_PORT ?? 8425);
const HOST = process.env.VIORIS_UI_HOST ?? "127.0.0.1";
const GATEWAY = process.env.VIORIS_GATEWAY_URL ?? "http://127.0.0.1:8420";
const EXTRA_HOSTS = (process.env.VIORIS_UI_ALLOWED_HOSTS ?? "").split(",").filter(Boolean);

if (!existsSync(path.join(dist, "index.html"))) {
  console.error(`No build found at ${dist}. Run: npm run build`);
  process.exit(1);
}

const app = express();
installSecurity(app, { extraHosts: EXTRA_HOSTS });
app.use(express.json({ limit: "1mb" }));
installProxy(app, { gatewayUrl: GATEWAY });

app.use(
  express.static(dist, {
    index: false,
    setHeaders(res, filePath) {
      if (filePath.includes(`${path.sep}assets${path.sep}`)) {
        res.setHeader("cache-control", "public, max-age=31536000, immutable");
      }
    },
  }),
);

app.get("*", (req, res, next) => {
  // Authenticated API calls were already handled by the proxy. A bare
  // `/devices` reaches here only as a browser navigation and gets the app.
  if (req.path.startsWith("/v1") || req.path.startsWith("/auth")) {
    return next();
  }
  res.setHeader("cache-control", "no-store");
  res.sendFile(path.join(dist, "index.html"));
});

app.listen(PORT, HOST, async () => {
  let gatewayState = "unreachable";
  try {
    const probe = await fetch(`${GATEWAY}/health`, { signal: AbortSignal.timeout(1500) });
    gatewayState = probe.ok ? "reachable" : `HTTP ${probe.status}`;
  } catch {
    gatewayState = "unreachable";
  }
  console.log(`Vioris UI  http://${HOST}:${PORT}`);
  console.log(`Gateway    ${GATEWAY} (${gatewayState})`);
  console.log("Bound to loopback only. Use Tailscale to reach it from a phone.");
});
