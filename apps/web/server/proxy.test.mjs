import assert from "node:assert/strict";
import { createServer } from "node:http";
import { request } from "node:http";
import test from "node:test";

import express from "express";

import { installProxy, installSecurity, isAllowedPath } from "./proxy.mjs";

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, "127.0.0.1", () => resolve(server));
  });
}

function ask(port, { method = "GET", path = "/", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = request(
      { hostname: "127.0.0.1", port, method, path, headers },
      (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () =>
          resolve({
            status: res.statusCode,
            headers: res.headers,
            body: Buffer.concat(chunks).toString("utf8"),
          }),
        );
      },
    );
    req.on("error", reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

function fakeGateway() {
  return new Promise((resolve) => {
    const seen = [];
    const server = createServer((req, res) => {
      const chunks = [];
      req.on("data", (chunk) => chunks.push(chunk));
      req.on("end", () => {
        seen.push({
          method: req.method,
          url: req.url,
          authorization: req.headers.authorization,
          secondFactor: req.headers["x-vioris-second-factor"],
          body: Buffer.concat(chunks).toString("utf8"),
        });
        res.setHeader("content-type", "application/json");
        res.end(JSON.stringify({ ok: true, path: req.url }));
      });
    });
    server.listen(0, "127.0.0.1", () => resolve({ server, seen, port: server.address().port }));
  });
}

async function buildApp({ gatewayPort, dev = false } = {}) {
  const app = express();
  installSecurity(app, { dev });
  app.use(express.json({ limit: "1mb" }));
  if (gatewayPort) installProxy(app, { gatewayUrl: `http://127.0.0.1:${gatewayPort}` });
  app.use((req, res) => res.status(404).json({ detail: "not proxied" }));
  const server = await listen(app);
  return { app, server, port: server.address().port };
}

test("the allowlist admits only the gateway contract", () => {
  for (const path of [
    "/health",
    "/v1",
    "/v1/tasks",
    "/v1/approvals/abc/approve",
    "/devices",
    "/devices/abc/revoke",
    "/auth/device/pair",
    "/auth/device/exchange",
  ]) {
    assert.equal(isAllowedPath(path), true, path);
  }
  for (const path of [
    "/",
    "/docs",
    "/app",
    "/v1/../../etc/passwd",
    "/v1\\tasks",
    "/devices/abc",
    "/auth/device/anything",
    "",
  ]) {
    assert.equal(isAllowedPath(path), false, path);
  }
});

test("the host guard refuses a DNS-rebinding style request", async () => {
  const { server, port } = await buildApp();
  try {
    const refused = await ask(port, { path: "/", headers: { host: "evil.example" } });
    assert.equal(refused.status, 403);
    assert.match(refused.body, /local hostname/i);

    const allowed = await ask(port, { path: "/", headers: { host: `127.0.0.1:${port}` } });
    assert.equal(allowed.status, 404);
    assert.equal(allowed.headers["x-frame-options"], "DENY");
    assert.match(allowed.headers["content-security-policy"], /script-src 'self'($|;)/);
  } finally {
    server.close();
  }
});

test("production CSP omits inline scripts, dev CSP permits the Vite preamble", async () => {
  const prod = await buildApp();
  const dev = await buildApp({ dev: true });
  try {
    const prodRes = await ask(prod.port, { headers: { host: `127.0.0.1:${prod.port}` } });
    assert.doesNotMatch(prodRes.headers["content-security-policy"], /script-src[^;]*unsafe-inline/);

    const devRes = await ask(dev.port, { headers: { host: `127.0.0.1:${dev.port}` } });
    assert.match(devRes.headers["content-security-policy"], /script-src[^;]*unsafe-inline/);
    assert.match(devRes.headers["content-security-policy"], /connect-src[^;]*ws:\/\/127\.0\.0\.1/);
  } finally {
    prod.server.close();
    dev.server.close();
  }
});

test("the proxy forwards a device key and the second factor, and nothing else", async () => {
  const gateway = await fakeGateway();
  const { server, port } = await buildApp({ gatewayPort: gateway.port });
  try {
    const res = await ask(port, {
      method: "POST",
      path: "/v1/approvals/abc/approve",
      headers: {
        host: `127.0.0.1:${port}`,
        "content-type": "application/json",
        authorization: "Bearer device-key",
        "x-vioris-second-factor": "123456",
        cookie: "should-not-travel=1",
      },
      body: JSON.stringify({ decision: "approve" }),
    });
    assert.equal(res.status, 200);
    assert.deepEqual(JSON.parse(res.body), { ok: true, path: "/v1/approvals/abc/approve" });
    assert.equal(res.headers["cache-control"], "no-store");

    const seen = gateway.seen.at(-1);
    assert.equal(seen.authorization, "Bearer device-key");
    assert.equal(seen.secondFactor, "123456");
    assert.equal(seen.body, JSON.stringify({ decision: "approve" }));
  } finally {
    server.close();
    gateway.server.close();
  }
});

test("an unauthenticated /devices navigation is left to the SPA, an API call is proxied", async () => {
  const gateway = await fakeGateway();
  const { server, port } = await buildApp({ gatewayPort: gateway.port });
  try {
    const navigation = await ask(port, {
      path: "/devices",
      headers: { host: `127.0.0.1:${port}`, accept: "text/html" },
    });
    assert.equal(navigation.status, 404); // reached the SPA fallback stub, not the gateway
    assert.equal(gateway.seen.length, 0);

    const apiCall = await ask(port, {
      path: "/devices",
      headers: { host: `127.0.0.1:${port}`, authorization: "Bearer device-key" },
    });
    assert.equal(apiCall.status, 200);
    assert.equal(gateway.seen.length, 1);
    assert.equal(gateway.seen[0].authorization, "Bearer device-key");
  } finally {
    server.close();
    gateway.server.close();
  }
});

test("a path outside the allowlist never reaches the gateway", async () => {
  const gateway = await fakeGateway();
  const { server, port } = await buildApp({ gatewayPort: gateway.port });
  try {
    const res = await ask(port, { path: "/v1/../secret", headers: { host: `127.0.0.1:${port}` } });
    assert.equal(res.status, 404);
    assert.equal(gateway.seen.length, 0);
  } finally {
    server.close();
    gateway.server.close();
  }
});

test("an unreachable gateway becomes an honest 502", async () => {
  const { server, port } = await buildApp({ gatewayPort: 1 });
  try {
    const res = await ask(port, { path: "/health", headers: { host: `127.0.0.1:${port}` } });
    assert.equal(res.status, 502);
    assert.match(JSON.parse(res.body).detail, /python run_vioris\.py/);
  } finally {
    server.close();
  }
});
