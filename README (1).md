# The Vioris

**A private, auditable, voice-first personal operating agent.**

Vioris is not a chatbot. It's a coordinated system — voice layer, planner, memory, specialist agents, connectors, a computer operator, a mobile command center, and a strict permission engine — that lets you run real work across your laptop, phone, accounts, and apps by talking to one thing.

> This repo's documentation is organized as a full build book. Read in this order if you're starting from zero:

| # | Doc | What's in it |
|---|---|---|
| 1 | [`docs/01-prd-and-personas.md`](./docs/01-prd-and-personas.md) | Product requirements, user personas, workflows, acceptance criteria |
| 2 | [`docs/02-architecture.md`](./docs/02-architecture.md) | Full system architecture, multi-agent design, data model, API spec |
| 3 | [`docs/03-permissions-and-security.md`](./docs/03-permissions-and-security.md) | Permission model, threat model, security requirements |
| 4 | [`docs/04-feature-catalog.md`](./docs/04-feature-catalog.md) | Exhaustive feature catalog — every capability, organized by domain |
| 5 | [`docs/05-roadmap-and-prompts.md`](./docs/05-roadmap-and-prompts.md) | Phase-by-phase build plan with ready-to-use vibe-coding prompts |
| 6 | [`docs/06-ui-design-system.md`](./docs/06-ui-design-system.md) | Figma design system + Google Stitch prompts for every screen |
| 7 | [`docs/07-business-and-gtm.md`](./docs/07-business-and-gtm.md) | Business models, target users, go-to-market, moat |
| — | [`SECURITY.md`](./SECURITY.md) | What Vioris must never do, incident handling |
| — | [`CONTRIBUTING.md`](./CONTRIBUTING.md) | Rules for adding any new tool/connector/agent |

## The one sentence that matters

> **Vioris may observe broadly. It must act narrowly. It must never act invisibly.**

Every feature in this book, no matter how ambitious, is designed to fit inside that sentence. That constraint is the actual product — the LLM underneath is replaceable, the trust layer isn't.

## Core loop

```
Listen → Understand → Clarify → Plan → Show risk → Ask approval → Execute → Verify → Report → Remember (only with permission)
```

## Repository layout

```
vioris/
├── apps/            desktop-agent, web-dashboard, mobile-app, browser-extension
├── services/        api-gateway, orchestrator, task-runner, voice, memory,
│                     knowledge, connector, notification, permission,
│                     verification, audit
├── agents/           orchestrator, computer, browser, research, communication,
│                     calendar, developer, documents, travel, smart-home,
│                     finance, memory, critic, verifier
├── packages/         shared-types, tool-schemas, auth, ui, logging, testing
├── prompts/          system, agents, safety, workflows
├── integrations/     email, calendar, storage, messaging, telephony, home
├── infra/            database, deployment, monitoring, secrets
├── docs/             this book
├── tests/            unit, integration, browser, desktop, safety
├── .env.example
├── SECURITY.md
├── CONTRIBUTING.md
└── README.md
```

## Status

Pre-Phase 0. Start with `docs/01-prd-and-personas.md`, lock the permission matrix in `docs/03`, then move phase by phase through `docs/05`.
