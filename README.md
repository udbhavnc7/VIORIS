# The Vioris

**A private, auditable, voice-first personal operating agent.**

Vioris is not a chatbot. It's a coordinated system — voice layer, planner, memory, specialist agents, connectors, a computer operator, a mobile command center, and a strict permission engine — that lets you run real work across your laptop, phone, accounts, and apps by talking to one thing.

> **Vioris may observe broadly. It must act narrowly. It must never act invisibly.**

Every feature in this repo is designed to fit inside that sentence. The LLM underneath is replaceable; the trust layer is not.

```
Listen → Understand → Clarify → Plan → Show risk → Ask approval → Execute → Verify → Report → Remember (only with permission)
```

## Run it

One command starts the whole local stack:

```bash
python run_vioris.py
```

Then open **http://127.0.0.1:8425**. The script starts the demo connectors, memory, task runner, computer agent, API gateway, and the web interface in dependency order and binds everything to `127.0.0.1`.

Requirements: Python 3.11+, Node.js 20+. On first run it installs and builds the interface. Useful flags:

```bash
python run_vioris.py --open                 # open the interface in your browser
python run_vioris.py --no-demo              # no sample connectors
python run_vioris.py --no-agent             # skip the computer agent
python run_vioris.py --critical-pin 1234    # allow Critical steps for this run
python run_vioris.py --no-ui                # backend only
```

| Service | Port | Role |
|---|---|---|
| Web interface | 8425 | React build served by a loopback-only Node proxy |
| API gateway | 8420 | the only service the UI may reach; auth, permissions, audit |
| Task runner | 8421 | planning, approvals, execution, verification, hash-chained audit |
| Memory | 8441 | local memory store |
| Computer agent | 8430 | screen mirror and remote input (optional) |
| Demo connectors | 8460 | sample read payloads; writes refuse honestly |

For phone access, put the laptop and the phone on a [Tailscale](https://tailscale.com) tailnet and pair the device from the UI. No public inbound port is ever opened. See [`docs/08-free-stack-and-remote-access.md`](./docs/08-free-stack-and-remote-access.md).

## Status

**Working v1 local preview.** The full loop runs end to end: a request is turned into a plan, every step is classified by a static permission registry (46 tools, tiers Observe / Prepare / Execute / Critical), Execute and Critical steps stop at an exact diff card for approval, approved steps run, and every outcome lands in an append-only, hash-chained audit log. The interface covers Overview, Workflows, Approvals, Activity, Memory, Connections, Remote devices, Call, and Settings.

What is deliberately simulated:

- **Connectors are honest demo mode.** Gmail, WhatsApp, Calendar, Contacts, Notes, Files, Bookmarks, History, Telephony, Reservations, and Smart Home return sample *reads* with a `demo: true` marker until real credentials are configured. Every *write* — send a message, create a reservation, start a call, control a device — is refused by the sample service and reported as failed in the audit chain. Nothing claims to have been sent when it was not.
- **Critical is locked by default.** Critical steps require `--critical-pin`, an HMAC second factor, and a five-second cooldown before the decision is accepted.
- **The computer agent is local-only.** Screen mirroring and remote input work over loopback/Tailscale; they are not exposed publicly.
- **No server-initiated phone calls.** The phone is a command surface, not an outbound autodialer.
- **Voice is text-first for now.** The conversation surface and speech-friendly copy are in place; local Whisper / Piper / openWakeWord are wired as an optional path, not a requirement.

## Why it is built this way

- **The model never assigns risk.** A static permission registry is the sole authority on whether a tool is Observe, Prepare, Execute, or Critical. An LLM cannot talk its way past it.
- **The frontend never executes.** It asks the authenticated gateway for work; the gateway, task runner, and permission engine decide.
- **Every action is auditable.** Audit events are append-only and hash-chained. The Activity screen reports chain integrity rather than hiding it.
- **Local-first, no paid APIs by default.** Ollama and the local services are enough to run the system; external models and apps are opt-in.
- **Secrets stay out of the model.** Tokens are never logged, never placed in prompts, and never handed to the planner.

## Repository layout

```
vioris/
├── apps/
│   ├── web/              React + TypeScript + Three.js interface,
│   │                     plus the loopback Node host/secure proxy (:8425)
│   ├── desktop-agent/    desktop shell
│   └── mobile-app/       Flutter mobile command surface
├── services/             api-gateway, orchestrator, task-runner, voice, memory,
│                         knowledge, connector, notification, permission,
│                         verification, audit, demo-connectors
├── agents/               orchestrator, computer, browser, research, communication,
│                         calendar, developer, documents, travel, smart-home,
│                         finance, memory, critic, verifier
├── packages/             shared-types, tool-schemas, auth, ui, logging, testing
├── prompts/              system, agents, safety, workflows
├── integrations/         email, calendar, storage, messaging, telephony, home
├── infra/                database, deployment, monitoring, secrets
├── docs/                 the build book
├── tests/                unit, integration, browser, desktop, safety
├── run_vioris.py         one-command local runtime
├── .env.example
├── SECURITY.md
└── CONTRIBUTING.md
```

## The build book

| # | Doc | What's in it |
|---|---|---|
| 1 | [`docs/01-prd-and-personas.md`](./docs/01-prd-and-personas.md) | Product requirements, personas, workflows, acceptance criteria |
| 2 | [`docs/02-architecture.md`](./docs/02-architecture.md) | System architecture, multi-agent design, data model, API spec |
| 3 | [`docs/03-permissions-and-security.md`](./docs/03-permissions-and-security.md) | Permission model, threat model, security requirements |
| 4 | [`docs/04-feature-catalog.md`](./docs/04-feature-catalog.md) | Exhaustive feature catalog by domain |
| 5 | [`docs/05-roadmap-and-prompts.md`](./docs/05-roadmap-and-prompts.md) | Phase-by-phase build plan with vibe-coding prompts |
| 6 | [`docs/06-ui-design-system.md`](./docs/06-ui-design-system.md) | Design system and screen prompts |
| 7 | [`docs/07-business-and-gtm.md`](./docs/07-business-and-gtm.md) | Business models, target users, go-to-market, moat |
| 8 | [`docs/08-free-stack-and-remote-access.md`](./docs/08-free-stack-and-remote-access.md) | Zero-cost stack and Tailscale remote access |

Also: [`SECURITY.md`](./SECURITY.md) for what Vioris must never do and how incidents are handled, and [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the rules that any new tool, connector, or agent must follow.

## Tests

```bash
python -m pytest -q                 # Python services, safety, permissions, audit
cd apps/web
npm run typecheck                   # TypeScript
npm test                            # Node proxy/security tests
npm run build                       # production interface build
```

The suite covers the permission registry, approval and Critical gates, audit chain integrity, pairing/auth, memory scoping, demo-connector honesty, proactive-call gating, and the Node host's allowlist, traversal refusal, DNS-rebinding guard, CSP, and SPA fallback. `tests/test_live_driving_flow.py` is an opt-in live script and skips unless `VIORIS_LIVE_TOKEN` is set.

## License

No license file is included yet. Treat the code as source-available and not for redistribution until a license is chosen.
