# 07 — Business Direction

## 1. Positioning

Not "another chatbot." Vioris's pitch:

> **A private, auditable operating agent that can safely perform real work across a user's devices.**

The defensible part isn't the underlying LLM — anyone can call an API for that. The moat is the permission engine, the audit trail, the memory layer, and the device-integration reliability: the things that make someone actually trust it with a real inbox and a real bank-linked booking flow.

## 2. Target Users (expansion order after your own use)

1. Students — deadline/email/calendar triage, low account risk to start with
2. Freelancers/developers — project memory, dev workflows, client communication triage
3. Small business owners — booking/reservation-heavy workflows, customer message triage
4. Researchers — multi-source research + citation-tracked reports
5. Remote workers — meeting prep, cross-timezone scheduling, async message triage
6. Families / accessibility-focused users — voice-first control for users who benefit from hands-free operation
7. Privacy-conscious professionals — self-hosted tier, nothing touching a third party's servers

## 3. Business Models to Evaluate

| Model | Shape |
|---|---|
| Free local-only version | Runs entirely on-device, no cloud sync, no subscription — builds trust and a user base |
| Paid cloud sync | Cross-device memory/task sync, hosted orchestration, subscription |
| Premium mobile remote control | Screen mirroring + remote input as a paid tier feature |
| Business workflow edition | Team-shared workflows, admin console, usage auditing for small businesses |
| Self-hosted privacy edition | One-time license, runs on the user's own infrastructure, zero data leaves it |
| Custom integrations | Paid, bespoke connectors for niche tools (CRMs, LMS platforms, internal company tools) |
| Automation marketplace | Users share/sell pre-built "trusted automation" workflow templates |
| Industry-specific editions | Education edition (deadline/LMS focus), developer edition (repo/CI focus), small-business edition (booking/customer-message focus) |

## 4. Go-To-Market Sketch

1. **Personal dogfood phase** — build for yourself through Phase 5 of the roadmap, using it daily before showing anyone.
2. **Campus-first soft launch** — the "read broadly, act narrowly" pitch is easy to trust for students specifically (low financial stakes, high message-triage pain). Use your own college as the first real test group.
3. **Open-source the core, monetize the edges** — publish the permission engine + orchestrator as open source to build credibility and attract contributors to connectors; monetize hosted sync, mobile remote-control, and self-host licensing.
4. **B2B pivot option** — the same permission engine, repackaged as an "AI action gateway" for companies wiring agents into internal tools, is a live market concern right now (every company doing agent rollouts needs exactly this kind of approval/audit layer) and could be a bigger business than the consumer product.

## 5. Moat, Stated Plainly

Not the model. Not the voice UI. The moat is:
- A permission engine trustworthy enough that users actually connect real accounts to it
- An audit system detailed enough to survive "what exactly did this thing do while I wasn't looking"
- Reliable device/connector integrations that don't silently break
- A memory system users can inspect and correct, so trust compounds over time instead of eroding after the first mistake

Anyone can wrap an LLM in a voice UI. Very few things survive being given send/pay/delete access to someone's actual life. That's the product.
