# 02 — System Architecture

## 1. High-Level Flow

```
Voice / Mobile / Web / Desktop
              ↓
        Vioris Gateway
              ↓
 Identity + Session + Permissions
              ↓
      Intent and Task Planner
              ↓
     Memory + Knowledge Retrieval
              ↓
       Tool and Agent Router
              ↓
 APIs | Browser | Desktop | Files | Phone | Smart Home
              ↓
       Verification and Audit Layer
              ↓
 Spoken reply + Mobile update + Activity log
```

## 2. Core Modules

| Module | Purpose | Suggested implementation |
|---|---|---|
| Wake service | Detects wake phrase, clap pattern, emergency command | openWakeWord/Porcupine, runs fully local |
| Voice service | STT, VAD, TTS, multilingual | Whisper (STT) + a fast TTS engine + local VAD |
| Vioris Core | Understands requests, coordinates system | FastAPI service wrapping an LLM with function-calling |
| Planner | Converts request → ordered, risk-tagged steps | LLM call constrained to a JSON step schema |
| Task manager | Runs/pauses/resumes/retries/cancels tasks | Postgres-backed state machine + a queue (Redis/Celery) |
| Tool router | Picks the right API/browser/desktop/agent for a step | Rule + LLM hybrid dispatcher |
| Computer agent | Operates the laptop, reads the screen | Local daemon; Playwright for browser; OS scripting for native apps |
| Browser agent | Navigates sites, fills forms, reads pages | Playwright, isolated automation profile (not your personal logged-in browser) |
| Memory system | Preferences, projects, conversations, decisions | Postgres + pgvector for semantic recall |
| Knowledge system | Indexes approved files/notes/emails | Embedding pipeline + pgvector, source-cited retrieval |
| Connector manager | Integrates external accounts | Per-service OAuth modules behind one interface |
| Permission engine | Decides confirmation/auth requirements | Deterministic rule table, see `03-permissions-and-security.md` |
| Verification engine | Confirms the action actually happened | Post-action state re-check per tool type |
| Notification service | Spoken / mobile / desktop / email updates | Push (FCM/APNs) + WebSocket + TTS |
| Security service | Encryption, pairing, tokens, sessions, logs | mTLS, JWT with short expiry, device pairing via QR |
| Admin console | Manage integrations, permissions, memory, devices | Web dashboard, backend-enforced, not just UI-hidden |

## 3. Multi-Agent Design

Don't route everything through one giant prompt — use a coordinator plus narrow specialists. Specialist agents never talk to each other directly; everything passes through the orchestrator, which makes the system debuggable and restrictable.

| Agent | Job | Tools it's allowed | Cannot do |
|---|---|---|---|
| **Orchestrator** | Understands request, delegates, merges results | Calls other agents only | Cannot itself call external APIs |
| **Research Agent** | Search, compare, cite | Web search, web fetch | Cannot act on findings |
| **Computer Agent** | Controls the laptop | OS commands, screen read, click/type | Cannot install software without Critical approval |
| **Browser Agent** | Browser-based tasks | Playwright session | Cannot bypass login walls/CAPTCHAs |
| **Communication Agent** | Reads/drafts/organizes messages | Email/messaging connectors (scoped) | Cannot send without Execute approval |
| **Calendar Agent** | Schedule and reminders | Calendar API | Cannot invite others without approval |
| **Developer Agent** | Code, tests, repos | Git, test runner, sandboxed shell | Cannot push to remote without approval |
| **Document Agent** | Reads/creates files | File system (allow-listed dirs) | Cannot delete without Critical approval |
| **Finance Agent** | Tracks expenses | Read-only transaction feeds | Cannot ever independently spend |
| **Travel Agent** | Journeys, reservations | Booking APIs/browser | Cannot pay without Critical approval |
| **Home Agent** | Smart-home control | Local IoT hub API | Cannot touch locks/alarms/security |
| **Memory Agent** | Decides what's worth remembering | Memory store | Cannot store sensitive-category data without explicit opt-in |
| **Critic Agent** | Reviews plans before execution | Read-only over the plan | Cannot execute anything — flags risk/missing info back to orchestrator |
| **Verifier Agent** | Confirms task actually succeeded | Read-only state checks | Cannot mark a task complete on its own — reports to task manager |

## 4. Permission Model (see doc 03 for full detail)

Four levels: **Observe → Prepare → Execute → Critical.** Every task step carries one of these; the permission engine — not the LLM's judgment — is the source of truth for whether execution pauses for approval.

## 5. Account/Access Reality Check

Vioris cannot legitimately access every app or private message automatically. Access always depends on: whether the service has an official API, whether your account tier is eligible, whether the service's ToS permits automation, and whether you've explicitly authorized the connection. WhatsApp/Instagram in particular are built around business-tier APIs — personal-account automation is more fragile (browser-session based) and needs its own ToS review before you wire it up. Vioris must never bypass passwords, 2FA, CAPTCHAs, or encryption to get access.

## 6. Data Model

```
User
Device
Session
AccountConnection
Permission
Task
TaskStep
Tool
ApprovalRequest
MessageSummary
Memory
Document
Project
Workflow
Notification
AuditEvent
ErrorReport
```

### Task object (canonical shape)

```json
{
  "task_id": "task_123",
  "request": "Check messages and summarize urgent items",
  "status": "running",
  "risk_level": "observe",
  "steps": [
    {
      "step_id": "step_1",
      "agent": "communication",
      "tool": "gmail.read_unread",
      "risk_level": "observe",
      "status": "done",
      "result": { "...": "..." }
    }
  ],
  "required_approvals": [],
  "connected_accounts": ["gmail:personal"],
  "created_at": "2026-08-08T09:00:00Z",
  "updated_at": "2026-08-08T09:00:04Z",
  "result": null,
  "audit_events": ["evt_1", "evt_2"]
}
```

### AuditEvent (append-only, hash-chained)

```json
{
  "event_id": "evt_1",
  "task_id": "task_123",
  "actor": "system | user | agent:communication",
  "action": "planned_step | requested_approval | approved | executed | verified | failed",
  "detail": { "...": "..." },
  "prev_hash": "abc123",
  "hash": "def456",
  "timestamp": "2026-08-08T09:00:01Z"
}
```

## 7. Core API Surface

```
POST   /auth/device/pair
POST   /voice/transcribe
POST   /tasks
GET    /tasks/:id
POST   /tasks/:id/pause
POST   /tasks/:id/resume
POST   /tasks/:id/stop
POST   /approvals/:id/approve
POST   /approvals/:id/reject
GET    /devices
POST   /devices/:id/lock
GET    /connections
POST   /connections/:provider/start
DELETE /connections/:id
GET    /memory
DELETE /memory/:id
GET    /audit-events
```

**Hard rule:** the frontend (web/mobile) never sends an email, makes a booking, or controls the laptop directly. It always requests a backend *task*. The backend authenticates, checks permissions, creates the audit record, and only then invokes the tool.

## 8. Repository Structure

```
vioris/
├── apps/
│   ├── desktop-agent/
│   ├── web-dashboard/
│   ├── mobile-app/
│   └── browser-extension/
├── services/
│   ├── api-gateway/
│   ├── orchestrator/
│   ├── task-runner/
│   ├── voice-service/
│   ├── memory-service/
│   ├── knowledge-service/
│   ├── connector-service/
│   ├── notification-service/
│   ├── permission-service/
│   ├── verification-service/
│   └── audit-service/
├── agents/
│   ├── orchestrator/ computer/ browser/ research/ communication/
│   ├── calendar/ developer/ documents/ travel/ smart-home/
│   └── finance/ memory/ critic/ verifier/
├── packages/
│   ├── shared-types/ tool-schemas/ auth/ ui/ logging/ testing/
├── prompts/
│   ├── system/ agents/ safety/ workflows/
├── integrations/
│   ├── email/ calendar/ storage/ messaging/ telephony/ home/
├── infra/
│   ├── database/ deployment/ monitoring/ secrets/
├── docs/
├── tests/
│   ├── unit/ integration/ browser/ desktop/ safety/
├── .env.example
├── SECURITY.md
├── CONTRIBUTING.md
└── README.md
```

## 9. Suggested Stack (matched to your existing toolkit)

- **Backend/orchestration:** FastAPI (Python) — you already use this in Vortex/PRISM
- **Task queue:** Celery + Redis — already in your Vortex stack
- **DB:** PostgreSQL + pgvector
- **Mobile:** Flutter — already used in Vaakya
- **Browser automation:** Playwright
- **LLM core:** function-calling capable model (Claude/Gemini) — you've used Gemini in Vaakya/PRISM
- **STT/TTS:** Whisper + a low-latency TTS engine
- **Telephony:** Twilio — already used in Vaakya for WhatsApp reporting
- **Realtime transport:** WebSocket over TLS between gateway and mobile/desktop
