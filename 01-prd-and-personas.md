# 01 — Product Requirements Document

## 1. Problem Statement

Managing a digital life today means switching between 15–30 apps, remembering which account has which permission, manually triaging messages across platforms, and doing repetitive multi-step workflows by hand (check calendar → check inbox → check messages → decide → act). No single assistant currently: (a) operates your actual computer, (b) is voice-first, (c) is provably safe enough to trust with real accounts, and (d) reports what it did in a way you can audit. Vioris exists to close that gap for one person at a time — you first, then potentially others.

## 2. Product Definition

| Field | Value |
|---|---|
| Name | The Vioris |
| Category | Personal operating agent |
| Primary interface | Voice |
| Secondary interfaces | Mobile app, desktop dashboard, browser extension, smartwatch, web console |
| Devices | Laptop (primary), phone, tablet, optional home server |
| Personality | Calm, concise, proactive, transparent, never presumptuous about risky actions |
| Operating principle | "Understand first. Confirm when necessary. Act carefully. Verify everything." |

## 3. User Personas

### Persona A — You (primary, v1 target)
Engineering student, self-directed, juggling coursework, hackathons, internship search, multiple side projects, several email/social accounts. Needs: daily triage across email/WhatsApp/college portal, project context-switching, deadline tracking, and a way to delegate "check and summarize" work so attention goes to actual building.

### Persona B — Developer / Freelancer (v2 expansion)
Works across multiple client repos and communication channels. Needs: project memory per client, safe terminal/test execution, deployment monitoring, invoice/expense tracking.

### Persona C — Busy Professional (v3 expansion)
Non-technical, wants a spoken daily briefing, meeting prep, and delegated bookings. Needs: extremely low-friction approval UX, since they won't tolerate a clunky confirmation flow.

### Persona D — Privacy-conscious power user (v3+ / self-host tier)
Wants everything above but self-hosted, no data leaving their own infrastructure, full audit export.

## 4. Primary Workflows (write acceptance tests against these)

1. **Morning briefing** — wake word → calendar + weather + urgent-message summary + deadline list, spoken and pushed to phone.
2. **Message triage** — "check WhatsApp/email and tell me what's important" → cross-account summary with named senders, urgency, and extracted asks.
3. **Delegated reply** — "reply to X" → drafted message shown for approval → sent only after explicit confirm.
4. **Resume project** — "open what I was working on" → correct app/repo opens, uncommitted-change summary given.
5. **Reservation** — "book a table for tonight" → clarify missing info → show full diff card (provider/time/price/cancellation) → confirm → book → verify.
6. **Remote supervision** — approve/reject a pending action from the phone while away from the laptop.
7. **Emergency stop** — say the kill phrase or tap the phone's stop button → every in-flight task halts within a bounded time (target: <2s).
8. **Memory correction** — "forget that" / "that's wrong, I actually prefer X" → memory updated or deleted, never silently retained.

## 5. Functional Requirements

- FR1: System must support wake-word, clap-pattern, hardware button, and phone-triggered activation.
- FR2: System must transcribe speech, extract intent + entities, and produce a visible step-by-step plan before any tool call.
- FR3: Every planned step must be tagged with a risk level (Observe / Prepare / Execute / Critical) before execution.
- FR4: Execute and Critical steps must pause for explicit user approval; Critical steps require a second factor (biometric/PIN).
- FR5: Every executed action must be followed by a verification check against real world state, not just absence of an error.
- FR6: All actions, plans, approvals, and results must be written to an immutable audit log, viewable and exportable by the user.
- FR7: A global stop command must be able to interrupt any running task tree from voice, hotkey, or phone.
- FR8: Memory must be categorized (preferences / people / projects / routines / sensitive) and independently viewable, correctable, and deletable per item.
- FR9: All third-party account access must go through OAuth with least-privilege, provider-appropriate scopes — no raw password storage.
- FR10: The frontend (web/mobile) must never call an external service directly; every consequential action routes through an authenticated backend task with permission checks.

## 6. Non-Functional Requirements

- **Security:** encrypted transport everywhere (TLS/mTLS), short-lived tokens, device pairing with revocation, no public port exposure for the laptop agent.
- **Reliability:** tasks must be pausable/resumable/retryable; a crashed agent must not leave an action half-done without recovery logic.
- **Observability:** every module emits structured logs; the audit trail must be reconstructable end-to-end for any task.
- **Latency:** wake-to-listening under 300ms; simple Observe-tier requests answered in under 3s where network-bound.
- **Privacy:** sensitive-category memory requires explicit per-item opt-in before storage; nothing sensitive is sent to third-party model providers without being clearly necessary for the task.
- **Accessibility:** high-contrast mode, reduced-motion mode, full keyboard navigation on desktop dashboard, screen-reader labels on mobile.

## 7. Out of Scope (v1)

- Unlocking physical doors or disabling security/alarm systems.
- Any medical, legal, or financial decision-making beyond tracking/summarizing.
- Fully autonomous spending without per-transaction confirmation.
- Circumventing CAPTCHAs, 2FA, or platform ToS to gain account access.
- Cross-user/multi-tenant features (v1 is single-user).

## 8. Definition of Success (v1 exit criteria)

- You can run a full day using only voice + phone approvals for: morning briefing, message triage, one delegated reply, one project resume, and one reservation — with zero unintended sends and a complete audit trail.
- Every Execute/Critical action in that day has a matching diff card and an approval record.
- Emergency stop verified to halt an in-flight multi-step task within 2 seconds in testing.

## 9. Acceptance Criteria Template (use per feature)

```
GIVEN <user context / connected accounts>
WHEN <voice or app command>
THEN <observable plan, risk classification, and approval behavior>
AND <verified end state>
AND <audit log entry exists with matching task_id>
```
