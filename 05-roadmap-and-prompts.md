
# 05 — Roadmap & Sequential Build Prompts

Each phase lists deliverables, an exit test, and multiple ready-to-paste prompts for your coding agent (Claude Code / Cursor / etc.). Run them in order — each assumes the previous phase's code exists in the repo. Keep prompts one-capability-at-a-time; a prompt that tries to build three phases at once is how permission bypasses sneak in.

---

## Phase 0 — Product Definition (no code)

**Deliverables:** PRD, personas, permission matrix, threat model, wireframes, MVP scope — all already drafted in docs 01–04 of this book. Lock these before writing code.

**Prompt 0.1 — Sanity-check the spec**
> "Read the attached PRD, permission matrix, and threat model for a personal agent called Vioris. List any ambiguities, missing edge cases, or places where the permission tiers are inconsistent, before I start implementation."

---

## Phase 1 — Safe Local Assistant (2–3 weeks)

**Deliverables:** desktop app, wake phrase, STT, TTS, basic commands, stop command, local task history. No external account access at all in this phase.

**Exit test:** "Hey Vioris, open VS Code and tell me the time" works end-to-end, and "Vioris, stop" reliably interrupts mid-response.

**Prompt 1.1 — Skeleton**
> "Set up a monorepo for Vioris: `/apps/desktop-agent` (Python), `/services/api-gateway` (FastAPI), `/packages/shared-types`. Add a `permission_engine.py` in a shared package that classifies an action dict into observe/prepare/execute/critical using a static registry (not an LLM call), and returns whether confirmation is required. Add a Postgres schema for an append-only, hash-chained `audit_events` table."

**Prompt 1.2 — Wake + voice loop**
> "In `/apps/desktop-agent`, add local wake-word detection (openWakeWord) for 'Hey Vioris' and a two-clap pattern. On activation, record audio, transcribe with Whisper, and log the transcript. Add TTS for spoken replies. Add a hard interrupt: saying 'stop' or 'Vioris stop' cancels any in-progress response within 500ms."

**Prompt 1.3 — Basic commands**
> "Add command handling for: 'what time is it', 'open <application>', 'set a reminder for <time>', and 'stop'. Do not implement any external network calls or account access yet. Persist a local SQLite/Postgres task history with each command, its classified risk level, and its result."

**Prompt 1.4 — Idle/listening/thinking/speaking states**
> "Add explicit state machine states (idle, listening, thinking, speaking, error, stopped) to the desktop agent, each with a distinct visual/audio cue. Write tests for: wake-word false positive during silence, cancellation mid-speech, and malformed/unintelligible commands."

---

## Phase 2 — Task Engine (2 weeks)

**Deliverables:** structured task objects, plans, pause/resume/retry, verification, approval requests.

**Exit test:** a task can be paused mid-plan from a CLI/API call, resumed later, and a deliberately-failing step retries without duplicating side effects.

**Prompt 2.1 — Task schema and lifecycle**
> "Implement a task engine in `/services/task-runner` using the Task/TaskStep schema from `docs/02-architecture.md`. Support create, start, pause, resume, cancel, retry, and fail-safely transitions. A task can only reach 'completed' after a verification step passes — never merely because a tool call returned without an error."

**Prompt 2.2 — Planner**
> "Add a `planner.py` in `/services/orchestrator` that takes a transcript, calls the LLM with function-calling enabled against a fixed tool schema, and returns an ordered list of TaskSteps, each pre-tagged with a risk level pulled from the static permission registry — the LLM proposes steps, it never sets their risk level. Return this plan as JSON before anything executes."

**Prompt 2.3 — Approval flow**
> "Wire the task engine so that any step tagged execute/critical pauses and emits a `pending_approval` event over WebSocket containing a full diff card (recipient/content/amount/account/date as applicable). Execution resumes only on an explicit `approved` event tied to that specific step's idempotency key, and is abandoned on `rejected`."

---

## Phase 3 — Controlled Computer Operation (3–4 weeks)

**Deliverables:** screen observation, application control, browser automation, safe file ops, allow-listed command execution.

**Exit test:** "open my last project" correctly restores the actual last-edited repo/app and reports real uncommitted changes; a deliberately risky command (e.g., delete) is blocked pending Critical approval.

**Prompt 3.1 — Computer agent core**
> "Implement `/agents/computer` as a local daemon: list open windows, open an application by name, capture a screenshot, and extract visible text via OCR/vision model. Add a verification step that re-checks screen state after every action and reports success/failure, not just 'command sent'."

**Prompt 3.2 — Browser agent**
> "Implement `/agents/browser` using Playwright in an isolated automation profile (not the user's personal logged-in browser session unless explicitly configured). Support: navigate to URL, read page text, fill a form field, click an element identified by description. Never attempt to bypass a CAPTCHA or login wall — surface it to the user instead and stop."

**Prompt 3.3 — File operations with guardrails**
> "Add file search/move/rename within an explicit allow-listed directory list (configurable, defaults to empty). Any delete, or any operation outside the allow-list, must be classified critical and blocked pending approval. Add an allowlist for terminal commands; anything not on the allowlist is refused, not attempted."

---

## Phase 4 — Memory & Personal Knowledge (2–3 weeks)

**Deliverables:** categorized memory (preferences/people/projects/routines/sensitive), document indexing, source-cited retrieval, correction/deletion UI.

**Exit test:** asking "what did I decide about X last week" returns an answer with a visible source and timestamp; deleting a memory item removes it from future answers immediately.

**Prompt 4.1 — Memory schema**
> "Implement `/services/memory-service` with a Postgres + pgvector schema separating preferences, people, projects, routines, and a sensitive category that requires an explicit `user_opted_in=true` flag before any write succeeds. Every record stores source, created_at, and confidence. Add endpoints to list, correct, and delete by id."

**Prompt 4.2 — Knowledge indexing + RAG**
> "Implement `/services/knowledge-service`: an ingestion pipeline for approved PDFs/notes/docs, chunking + embedding into pgvector, and a retrieval endpoint that returns answers with the source document and location cited. If no relevant chunk is found above a similarity threshold, return 'not found in your indexed material' instead of guessing."

---

## Phase 5 — Phone Control Center (3 weeks)

**Deliverables:** Flutter app, secure pairing, live status, approvals, remote screen, remote stop/lock.

**Exit test:** you can approve a pending draft reply from your phone while the laptop is closed and out of sight.

**Prompt 5.1 — Pairing and gateway**
> "Implement `/services/api-gateway` device pairing: generate a short-lived QR-encoded pairing token, exchange it for a device-scoped JWT, and reject any WebSocket connection from an unpaired or revoked device. Add a devices list + revoke endpoint."

**Prompt 5.2 — Mobile app v1**
> "Scaffold `/apps/mobile-app` in Flutter with three screens: Home (push-to-talk + status), Approval Queue (diff cards with Approve/Reject buttons), and Activity Timeline. Connect over the paired WebSocket. Add a persistent 'Stop Everything' control reachable from every screen."

**Prompt 5.3 — Remote screen + input**
> "Add screen-mirroring from the desktop agent to the mobile app (throttled, on-demand only, never continuously streaming) and remote keyboard/mouse input classified as an execute-tier action requiring explicit 'start remote session' approval, auto-expiring after a configurable timeout."

---

## Phase 6 — Integrations (build in this order, 1 connector at a time)

Order: Calendar → Email (read-only) → Cloud files → Notes/documents → Contacts → Browser bookmarks/history → Approved messaging → Calling → Reservations → Smart-home.

**Prompt 6.x — Generic connector template (reuse per service)**
> "Create a connector for [SERVICE] in `/integrations/[service]`. Use the official API where one exists. Use OAuth with the minimum viable scope, and keep read and write scopes separate. Store tokens encrypted at rest, never exposed to the LLM context. Support connect, refresh, revoke, disconnect. Log every access as an audit event. Add a mock provider for tests. Handle rate limits and expired sessions explicitly. Add a plain-language permission explanation string surfaced in the UI. This connector must not send, delete, purchase, publish, or modify anything without going through the permission engine — no shortcuts, even for 'safe-seeming' actions."

**Prompt 6.1 — Gmail (read-only first)**
> "Implement the Gmail connector using OAuth scope `gmail.readonly` only. Add an endpoint that fetches unread mail from the last 24 hours and returns sender, subject, an urgency estimate, and any extracted dates/asks. Do not request send scope in this phase."

**Prompt 6.2 — WhatsApp digest**
> "Implement a WhatsApp connector appropriate to [WhatsApp Business API access / browser-session approach — pick based on account eligibility researched in Phase 0]. Produce a digest in this exact shape: sender name, one-line summary of the message, and any explicit ask (e.g., 'asking about flowers', 'requesting student information'). Flag anything the connector cannot access due to platform restrictions rather than silently omitting it."

---

## Phase 7 — Specialist Agents

**Prompt 7.x — Generic specialist agent template**
> "Create the [AGENT NAME] agent in `/agents/[name]`. It receives structured tasks from the orchestrator only — never directly from the user. It may use only its explicitly assigned tools. It must return a structured result plus a human-readable summary. It must state uncertainty rather than guessing, request missing information via the orchestrator rather than assuming it, never bypass the permission engine, and never claim success without a passing verification check. Include citations/source references wherever the output depends on external data."

---

## Phase 8 — Testing & Launch

**Required test categories:** unit, integration, browser, desktop-control, permission-boundary, account-revocation, prompt-injection, voice false-activation, network-failure, duplicate-action/idempotency, crash-recovery, mobile-security, human-approval-flow.

**Prompt 8.1 — Safety test suite**
> "Write a safety test suite covering: (a) a malicious webpage/email attempting to inject an instruction into a summarization task — verify it is never executed as a command; (b) a rejected approval — verify the corresponding action never fires; (c) a duplicate approval double-tap — verify idempotency keys prevent a duplicate send/booking; (d) an expired session mid-task — verify it forces re-auth rather than silently proceeding; (e) the emergency stop halting a multi-step in-flight task within 2 seconds."

**Launch modes — ship in this order, don't skip to the end:**
1. **Observe mode** — read/summarize/suggest only.
2. **Supervised mode** — acts, but every execute/critical step needs approval. Target default for v1 real-world use.
3. **Trusted automation mode** — a small, manually-promoted whitelist of low-risk repeatable workflows run without per-instance approval.
