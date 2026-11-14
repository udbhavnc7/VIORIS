import express from "express";
import { createServer as createViteServer } from "vite";

import { installProxy, installSecurity } from "./proxy.mjs";

const PORT = Number(process.env.VIORIS_UI_PORT ?? 5173);
const HOST = process.env.VIORIS_UI_HOST ?? "127.0.0.1";
const GATEWAY = process.env.VIORIS_GATEWAY_URL ?? "http://127.0.0.1:8420";

const app = express();
installSecurity(app, { dev: true });
app.use(express.json({ limit: "1mb" }));
installProxy(app, { gatewayUrl: GATEWAY });

const vite = await createViteServer({
  server: { middlewareMode: true, hmr: { port: PORT + 1 } },
  appType: "spa",
});
app.use(vite.middlewares);

app.listen(PORT, HOST, () => {
  console.log(`Vioris UI (dev)  http://${HOST}:${PORT}`);
  console.log(`Gateway          ${GATEWAY}`);
});
