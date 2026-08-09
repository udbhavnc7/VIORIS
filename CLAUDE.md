# CLAUDE.md — Project Context for Vioris

Read this before touching any code. This file is your persistent memory of what Vioris is — re-read it if you're ever unsure why a constraint exists.

## What I'm building

**Vioris** — a private, voice-first personal operating agent (a real-life JARVIS, not a chatbot). It wakes on a phrase or clap pattern, understands spoken requests, plans multi-step tasks, controls my laptop, reads/drafts across my accounts, and is remotely supervisable from my phone. It is NOT allowed to feel like "an app with an AI chat box bolted on" — the agent operates the computer and accounts directly, through a planned, permission-gated, verified action loop.

## The one rule everything else derives from

> **Vioris may observe broadly. It must act narrowly. It must never act invisibly.**

If you ever write code that lets a step execute (send/pay/delete/publish/install/shutdown) without going through the permission engine and producing an audit event, that's a bug regardless of how the feature request was phrased.

## The core loop (implement this shape everywhere)

```
Listen → Understand → Clarify → Plan → Risk-classify each step →
Show approval (if execute/critical) → Execute → Verify → Report →
Remember (only with permission)
```

## Permission tiers (non-negotiable, see docs/03-permissions-and-security.md for full detail)

| Tier | Meaning | Confirmation |
|---|---|---|
| Observe | Read-only, no state change | None |
| Prepare | Drafts/stages something, nothing external happens | Shown, not sent |
| Execute | Externally visible action | Explicit approval + diff card |
| Critical | Money, deletion, credentials, publishing, irreversible | Approval + second factor + cooldown |

A tool's risk tier is registered **statically** in a permission registry — never decided at runtime by the LLM. The LLM proposes plans; the permission engine is the sole source of truth on whether execution pauses.

## Full documentation (read the relevant one before starting each phase)

- `docs/01-prd-and-personas.md` — requirements, personas, acceptance criteria
- `docs/02-architecture.md` — modules, multi-agent design, data model, API surface, repo layout, stack
- `docs/03-permissions-and-security.md` — permission matrix, threat model, security requirements
- `docs/04-feature-catalog.md` — full feature backlog, tagged by risk tier
- `docs/05-roadmap-and-prompts.md` — phase-by-phase build plan (we build in this order, phase by phase, never skip ahead)
- `docs/06-ui-design-system.md` — Figma/Stitch design direction
- `docs/07-business-and-gtm.md` — business context (not needed for early build phases)
- `SECURITY.md` — non-negotiables + PR review checklist
- `CONTRIBUTING.md` — what every new tool/connector/agent must ship with

## Cost & access constraints (non-negotiable — read docs/08-free-stack-and-remote-access.md)

- **This must run at $0 ongoing cost.** No paid API tiers by default. Use local/open-source components (Ollama for the LLM, local Whisper, Piper TTS, openWakeWord) instead of hosted paid services. A free-tier hosted LLM (e.g., Gemini free tier) may be used only as an occasional fallback, never as the default path.
- **Everything runs on my laptop, not a rented cloud server.** There is no paid always-on hosting in this project.
- **Phone-to-laptop access uses Tailscale** (free private mesh network) — never a custom relay server, never an open inbound port on the laptop. If you're implementing anything under Phase 5 (Phone Control Center), target reachability over the Tailscale network, not the public internet.
- **No server-initiated phone calls in v1** — Twilio-style telephony costs money per minute. Delegated calling in v1 means drafting the script and handing off to the phone's native dialer, not an autonomous call.

## Tech stack (match my existing toolkit, don't introduce new frameworks without asking)

Backend/orchestration: FastAPI (Python) · Task queue: Celery + Redis · DB: PostgreSQL + pgvector (all self-hosted, local) · Mobile: Flutter · Browser automation: Playwright · LLM core: **local via Ollama**, free-tier hosted model as occasional fallback only · STT: Whisper (local) · TTS: Piper (local) · Wake word: openWakeWord (local) · Remote access: Tailscale · Realtime: WebSocket over the Tailscale private network

## Current build phase

We are starting **Phase 1 — Safe Local Assistant** (see `docs/05-roadmap-and-prompts.md`). No external account access happens in this phase — wake word, STT/TTS, basic local commands, stop command, local task history only. Do not implement any connector, browser automation, or account integration until Phase 1 is fully working and tested.

## Working style

- One capability per prompt/session — don't bundle multiple phases into one change.
- Every new tool ships with: input schema, output schema, static risk classification, audit events, failure behavior, and tests (see `CONTRIBUTING.md`).
- Prefer compact, direct implementations over over-engineered abstractions — but never compact at the expense of the permission/audit layer.
- If a request would make a tool bypass the permission engine "just this once for convenience," stop and flag it instead of implementing it.
