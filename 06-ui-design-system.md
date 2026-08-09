# 06 — UI Design System

## 1. Visual Direction

- Dark graphite background, electric violet + cyan accents, subtle glassmorphism panels
- Status colors used consistently everywhere: green = success, amber = approval needed, red = danger/stopped
- Circular voice-orb as the central listening/thinking/speaking indicator
- Minimal sci-fi styling — restrained animation, not decorative noise
- High-contrast accessibility mode + reduced-motion mode as first-class toggles, not afterthoughts

## 2. Figma File Structure

Create one design-system file, `Vioris — Design System`, with these pages:

1. Brand & visual language
2. Color tokens (base palette + semantic tokens: success/warning/danger/info)
3. Typography scale
4. Buttons & controls
5. Voice states (idle / listening / thinking / speaking / error / stopped)
6. Cards & panels (component library below)
7. Mobile screens
8. Desktop screens
9. Empty / loading / success / error / approval states
10. Security & permission screens
11. Prototype flows (wire the Approve/Reject path end-to-end here first)

### Component library

`StatusPill` · `DiffCard` · `TimelineStep` · `RiskBadge` (observe/prepare/execute/critical, color-coded) · `VoiceOrb` (animated waveform) · `ApprovalModal` · `ConnectionCard` (per integration, showing exact scopes) · `MemoryItem` (with source + delete control) · `DeviceCard`

**Design order matters:** prototype the Approve/Reject flow with *realistic* diff-card content before writing any frontend code. This is where you catch UX problems — like an unclear risk level or a diff card that hides the actual recipient — while it's still cheap to fix.

## 3. Desktop Dashboard Screens

1. **Command center** — voice orb, greeting, time, device connection status, daily briefing, active task card, approval queue, important messages, upcoming calendar events
2. **Live computer-control screen** — laptop screen preview, action timeline, pause/stop buttons, plain-language explanation of the current step
3. **Connected apps & permissions** — per-integration scope list, connect/disconnect
4. **Personal memory manager** — browsable by category, correct/delete per item, source shown
5. **Automation builder** — promote a workflow from Supervised to Trusted-automation mode
6. **Security center** — devices, sessions, permissions, audit logs, emergency lock
7. **Project workspace** — coding-task view: diffs, test results, logs

## 4. Mobile Screens

1. Voice-first home screen (push-to-talk, status line)
2. Live task monitoring
3. Approval request (diff card, full detail, Approve/Reject)
4. Remote laptop control (screen mirror + input)
5. Notifications & briefings
6. Settings & privacy center (proactivity level, connected accounts, memory, devices)

## 5. Required States (design all of these, not just the happy path)

Loading, success, failure, offline/laptop-disconnected, expired authentication, confirmation-required, dangerous-action-blocked, empty (no tasks yet), partial-failure (some steps succeeded, one didn't).

## 6. Google Stitch Prompt — full app

```text
Design a polished desktop and mobile interface for "The Vioris," a private personal operating agent.

Visual direction:
Dark graphite interface, electric violet and cyan accents, subtle glassmorphism, premium
futuristic assistant aesthetic, high readability, clean spacing, restrained animations,
accessible high-contrast option.

Desktop screens:
1. Command center — voice orb, greeting, time, device connection status, daily briefing,
   active task card, approval queue, important messages, upcoming calendar events.
2. Live computer-control screen — laptop screen preview, action timeline, pause button,
   stop button, current task explanation in plain language.
3. Connected apps and account permissions, showing exact granted scopes per integration.
4. Personal memory manager, browsable by category, with per-item delete and source shown.
5. Automation builder for promoting a workflow to trusted, no-approval-needed status.
6. Security center — devices, sessions, permissions, audit logs, emergency lock button.
7. Project workspace for coding tasks — diff view, test results, logs.

Mobile screens:
1. Voice-first home screen.
2. Live task monitoring.
3. Approval request screen with a full diff card (recipient/content/amount/account/date).
4. Remote laptop control (screen mirror + input).
5. Notifications and daily/evening briefings.
6. Settings and privacy center.

Create realistic states for: loading, success, failure, offline mode, expired authentication,
confirmation required, and dangerous action blocked. Use clear labels, no ambiguous icons —
every risky action must be self-evidently risky from the visual design alone.
```

## 7. Google Stitch Prompt — approval flow only (use this to iterate fast)

```text
Design a mobile approval-request screen for an AI agent app called Vioris. Show a card
with: a risk-level badge (color-coded: green=observe, blue=prepare, amber=execute,
red=critical), a one-line plain-language description of the action, and an expandable
"exact details" section showing recipient, message text, amount, account, and date as
applicable. Two large buttons: Approve and Reject. For critical-risk actions, add a
biometric/PIN confirmation step after Approve is tapped, before the action actually fires.
Dark theme, graphite background, violet and cyan accents.
```

## 8. Prompt for Figma Make / a coding agent (frontend scaffold)

```text
Build the Vioris dashboard from the approved Figma design.

Requirements:
- Responsive desktop and mobile layouts.
- Voice command input with listening/processing/speaking/idle states.
- Live task timeline with per-step status.
- Confirmation modal (DiffCard) showing exact recipient, content, account, cost, and consequence.
- Approve, reject, edit, pause, resume, stop controls.
- Device connection status indicator.
- Connected-app permission management screen showing exact scopes.
- Full activity history, filterable and exportable.
- Dark theme, violet and cyan accents, high-contrast toggle, reduced-motion toggle.
- Full keyboard navigation and accessible labels throughout.
- Use mock data only until backend APIs are connected — do not fabricate a "connected"
  state for any integration that isn't actually wired up.
- The frontend must never execute an external action directly — every button that triggers
  a consequential action calls an authenticated backend task endpoint, full stop.
```
