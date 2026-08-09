# 03 — Permission Model & Threat Model

## 1. The Permission Matrix (source of truth)

| Level | Definition | Examples | Confirmation |
|---|---|---|---|
| **Observe** | No state change anywhere | Read messages, search files, inspect calendar, summarize | Allowed by default |
| **Prepare** | Creates a draft/artifact, nothing external happens | Draft an email, stage a code diff, price a booking | Allowed, always shown before it's "ready" |
| **Execute** | Externally visible, generally reversible | Send a message, make a call, book something, edit/upload a file | Confirmation required, exact payload shown |
| **Critical** | Money, deletion, credentials, publishing, irreversible | Pay, delete, change passwords, unlock, share sensitive data, publish publicly | Confirmation + secondary auth (biometric/PIN) + a short cooldown before it actually fires |

**Rule:** the permission engine is deterministic and lives in the backend — never inferred fresh by the LLM per-request. A tool's risk level is a property of the *tool*, registered at build time, not decided at call time. The LLM can *propose* a plan; it cannot *reclassify* a tool's risk level.

## 2. Approval UX Contract

Every Execute/Critical step must render a **diff card** containing, verbatim, whichever of these apply:
- Exact recipient(s)
- Exact message/content text
- Exact amount and currency
- Exact account/identity being used
- Exact date/time
- Cancellation/reversal policy, if any
- What happens if approved vs. rejected

No vague "sending your message now" — the user must see precisely what will happen before it happens.

## 3. Account & Data Categories in Memory

| Category | Examples | Storage rule |
|---|---|---|
| Preferences | Writing style, favorite places | Stored by default |
| People | Contacts, relationships, tone-per-contact | Stored by default |
| Projects | Coding/academic/business context | Stored by default |
| Routines | Daily briefing time, recurring workflows | Stored by default |
| Sensitive | Health, financial specifics, government IDs, anything the user flags as private | **Never stored without explicit, per-item opt-in** — and never sent to a third-party model provider unless strictly necessary for the task, with the user told so |

Every memory record stores: source, creation time, confidence, and is independently correctable/deletable. When Vioris uses a memory in a response, it should be able to say where that fact came from.

## 4. Threat Model

| Threat | Mitigation |
|---|---|
| Prompt injection from a webpage/email content | Treat all fetched content as **data, never instructions** — the planner only accepts commands from the authenticated user's voice/text input, never from tool output |
| Stolen/leaked long-lived tokens | Short-lived OAuth tokens, least-privilege scopes, automatic refresh/expiry, tokens never exposed to the LLM context directly |
| Laptop exposed to the public internet | Never open inbound ports on the laptop; all phone↔laptop traffic goes through an authenticated relay/tunnel (e.g., a Tailscale-style private network) |
| Wrong-contact / wrong-recipient send | Diff card always shows the resolved contact identity, not just the name typed; ambiguous name matches force a clarifying question before drafting |
| False wake-word activation | Local wake-word confidence threshold + a visible "I'm listening" indicator + short listening window with auto-cancel |
| Duplicate bookings/sends on retry | Idempotency keys on every Execute/Critical tool call |
| Malicious attachment/link in a summarized message | Never auto-open/auto-download attachments; only open after explicit Execute-level approval |
| Unauthorized phone access to the control app | Device pairing with per-device revocation, biometric app lock, session timeout |
| Model hallucinating a "successful" action | Verifier Agent independently re-checks real-world state — a task is never marked done off the tool call's own return value alone |
| Silent scope creep (a connector doing more than it says) | Every connector ships an explicit scope declaration reviewed in `CONTRIBUTING.md`'s checklist; scopes are enforced server-side, not just documented |
| Someone else in the room triggering actions | Optional speaker verification gates Execute/Critical tiers to your voice specifically |

## 5. Security Requirements

- TLS/mTLS on all network hops; no plaintext transport anywhere.
- OAuth with least-privilege, provider-appropriate scopes — never raw password storage for any connected account.
- Device pairing via short-lived pairing codes (QR + JWT), with per-device revocation from the dashboard.
- Audit log is **append-only and hash-chained** — no action can be silently edited or removed after the fact, and the chain lets you detect tampering.
- Sessions expire; expired sessions force re-auth rather than silently failing or silently extending.
- Local-first wake-word detection — nothing about your voice leaves the laptop until the wake phrase actually fires.
- Emergency stop must work even if the orchestrator is mid-plan: implemented as a hard interrupt at the task-runner level, not a request the LLM has to "notice."

## 6. What Vioris Must Never Do

- Bypass authentication, 2FA, or CAPTCHAs.
- Access an account without explicit authorization.
- Send a message, make a payment, or publish anything without the relevant approval tier being satisfied.
- Delete files/emails/data without Critical-tier confirmation.
- Impersonate the user deceptively — any AI-delegated call or message must disclose it's from Vioris on the user's behalf.
- Unlock doors, disable alarms, or make any safety-critical change from voice alone.
- Claim a task succeeded without the Verifier Agent independently confirming it.
- Hide or omit an action from the audit log, even a failed or rejected one.

## 7. Launch Modes (don't ship unrestricted autonomy)

1. **Observe mode** — Vioris can read, summarize, and suggest only. Good for the first week of daily use.
2. **Supervised mode** — Vioris can act, but every Execute/Critical step needs your approval. This is the default target for v1.
3. **Trusted automation mode** — a small, explicitly whitelisted set of low-risk, repeatable workflows (e.g., "always draft — never send — a reply to my professor") can run without per-instance approval, but only after you've manually promoted that specific workflow out of Supervised mode.
