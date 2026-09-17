# Vioris — Roadmap to "Best Personal AI Agent" (Still $0)

You're closer than 25-30% would suggest. Phases 1-6 give you the *skeleton* of a real agentic OS — voice loop, planner, permission engine, memory, phone control, connectors. What's missing isn't more connectors. It's the handful of things that separate "a working agent" from "an agent you'd actually trust to touch your laptop while you're asleep." That's where the real 70% is.

This doc is split into five parts:
0. **The actual unlock** — how to get the "calls you while driving" experience you're picturing, fully within your $0 / no-Twilio constraint
0.5. **The conversation itself** — what it actually takes to hold the multi-intent, decision-surfacing exchange in your example, not just place the call
1. **What to add, and why it matters** (not generic feature bloat — things that directly serve your own core rule)
2. **The free-stack upgrades** that make it faster and more capable without spending a rupee
3. **A revised phase plan + sequential prompts** you can paste into your coding sessions one at a time, in your existing "one capability per prompt" style

One honest note up front: nobody can hand you a guaranteed billion-dollar idea — that's a function of execution and distribution, not just concept. What I can tell you is that a genuinely trustworthy, voice-native agent that calls you and acts rather than just chats is a real gap — most funded "AI agent" products, including ones with real money behind them, are still chat-window-shaped. Nailing the call-initiated, permission-gated version below is a legitimately strong, differentiated bet. That's the honest version of "blows people's minds."

---

## Part 0 — The Real Unlock: Vio That Calls You, At $0

Your own CLAUDE.md rules this out as written — "No server-initiated phone calls in v1 — Twilio-style telephony costs money per minute." That rule is correct for real PSTN calls. But the experience you described doesn't actually need a real phone call. It needs something better: **a native in-app "call" between Vio and you, riding over the Tailscale connection and Flutter app you've already planned.** No telephony API, no per-minute cost, no Twilio account — and it can do things a real phone call can't (live transcript, pull up the actual email on screen if you glance down, log the whole exchange to the audit chain automatically).

**How it works, concretely:**

- **The "ring."** The backend pushes an event over the already-open WebSocket (or a wake notification if the app isn't foregrounded) that tells the Flutter app to show a full-screen incoming-call UI — the same pattern WhatsApp/Signal use for VoIP calls. Android: a full-screen intent notification. iOS: CallKit's native call UI, which Apple explicitly allows VoIP-style apps to use for free (the only cost is the standard $99/yr Apple developer account if you don't already have one — flagging that honestly, it's not literally $0, but it's not per-call/per-minute either).
- **The "call."** Once you answer, audio streams both directions over the same channel: Piper TTS output plays through the phone speaker/car Bluetooth, your voice gets captured and sent back for Whisper to transcribe. Start with raw audio chunks over the existing WebSocket — simpler than WebRTC, and Tailscale's private mesh keeps latency low. Only move to WebRTC later if you notice lag.
- **Reachability.** Tailscale isn't LAN-only — it does NAT traversal over the public internet through its coordination servers, so this works even when your phone is on mobile data away from home, which is exactly the "while driving" case.
- **Driving safety.** Because it's audio-first with one big "answer" tap, you never need to look at the screen. Car Bluetooth picks up the app's audio automatically, same as any call or music app — no CarPlay/Android Auto integration needed for this to work.

**The three specific capabilities your example actually exercises — worth designing as first-class tools:**

1. **Contact + relationship resolution.** For Vio to know a name refers to a specific person on a specific channel (WhatsApp vs SMS) without you spelling it out, extend the Phase 6 Contacts connector with a lightweight relationship field in semantic memory ("X → spouse → prefers WhatsApp"). Small addition, big payoff in how natural the interaction feels.
2. **"React" as a real action, not a metaphor.** Gmail has no native thumbs-up reaction, so decide what it maps to — a custom "Acknowledged" label, star + mark-read, or a canned one-line reply. Whatever you pick, register it as its own tool with its own risk tier (probably Prepare — low-stakes but externally visible) rather than folding it into a generic "reply" tool.
3. **Proactive status digest.** "Your project has completed so many phases, want to continue?" is Vio initiating contact based on its own task-history awareness — this is the opt-in routine-suggestion idea from Part 1, elevated into a schedulable trigger ("call me when a phase finishes," "check in every evening at 7") rather than something that only surfaces passively in-app.

This is the piece I'd build first, honestly — before more connectors, before more phases. It's the single feature that turns Vio from "an agent you open an app to talk to" into "an agent that's present in your life," and it costs nothing beyond what you've already scoped.

---

## Part 0.5 — The Conversation Itself: What It Actually Takes to Hold That Exchange

Placing the call is the easy half. Your example isn't just "Vio calls you" — it's Vio holding a real conversation: multiple questions in one breath, a bundled command touching two different targets, a decision surfaced instead of auto-decided, a natural close. Nothing in Phases 1-8 as scoped produces that on its own. Your Phase 2 planner is built around one request → one task → one approval. What you described needs a layer above it.

**Your script, read as a spec:**

- *"Mr Chandragiri, your project LUME has completed so many phases, do you wanna continue with the next phase?"*
  Status, then an explicit yes/no gate — Vio never assumes "continue" even though it placed the call. Your core rule, applied to the opening line itself. (Small polish detail, not required for v1: it opens formal since it's an unprompted call, and could relax to your first name once you respond — worth a deliberate choice, not an accident.)

- *"Yes, go on, also tell me which mails have I received, are there any texts from Disha?"*
  One utterance, three intents: approve the pending phase, query unread mail, query texts filtered by sender. A single sentence has to fan out into multiple parallel sub-requests — something a "one request, one task" planner doesn't do by default.

- *"...currently one important mail... also your wife Disha has sent a text... what do I reply to the mail and to your wife?"*
  Two read-only results merged into one spoken summary, composed as prose, not read out as a list. And critically: Vio notices both items look like they want a response and surfaces that as a single bundled question, without drafting or sending anything. That's a judgment call made at Observe tier — it flags, it never acts.

- *"Don't reply to the mail, just react with a thumbs up, and also tell Disha I'll be reaching in another hour."*
  One utterance, three instructions across two separate targets — a negation, a substitute action, and an unrelated third action in the same breath. The system has to keep the mail-thread and the text-thread separate so the "don't" on one doesn't bleed into the other.

- *"Sure Udbhav, I have sent the message and reacted to mail, is there anything else I need to do?"*
  Both confirmations composed into one sentence, then the call is handed back, not ended — the way an actual phone call closes.

**The piece you're missing: a Dialogue Orchestrator**, sitting between the voice layer and the Phase 2 task planner:

- **Multi-intent decomposition** — split one utterance into N sub-intents and dispatch each to its own tool/agent, instead of forcing everything through a single task.
- **Call-scoped reference memory** — "the mail," "her," "that" resolve against what was just said in *this* call. Short-lived, separate from your long-term semantic/episodic store.
- **Actionability flagging, never auto-action** — a lightweight pass (can reuse the Phase 7 small routing model) that looks at a read-only result and flags "this looks like it wants a reply," and stops there. It surfaces the question; it never drafts the answer unasked.
- **Compound command splitting** — parse one reply into multiple independent instructions, each routed to its own tool call with its own risk tier.
- **Response composition** — a dedicated generation step that turns N tool results into one natural spoken sentence, not concatenated fragments.

**How this plugs into the permission engine — the trust envelope for a live call:** your voiceprint idea from Part 1 and this orchestrator solve each other's hardest problem. A live call is hands-free by definition — there's no diff card to tap while driving. So: **verify the voiceprint once at pickup, and treat the rest of that call as a verified session.** Execute-tier actions (send the text, react to the mail) proceed on a spoken "yes" without re-verifying each time, because you already confirmed it's you when you answered. Critical-tier actions still demand a fresh, explicit spoken confirmation mid-call — a distinct confirm phrase — so a 45-minute drive never quietly becomes a standing authorization for anything irreversible. Worth deciding deliberately, while you're at it: under your own tier table, a *sent* message is arguably irreversible the moment it's delivered — pick, on purpose, whether "send a text/email" sits at Execute or Critical in your registry, rather than leaving it implicit.

This orchestrator is genuinely the hardest and most valuable thing in this whole document. The connectors, the phases, the infrastructure — that's standard agent engineering, and plenty of well-funded products have it. A dialogue layer that holds a real multi-intent, reference-aware, decision-surfacing conversation on top of an actual permission system is not something the current wave of "AI agent" products has nailed. That's the part worth being genuinely proud of.

---

## Part 1 — What Actually Makes This "The Best"

Your core rule is: *"Vioris may observe broadly. It must act narrowly. It must never act invisibly."* Almost everything below is that rule, taken more seriously than most agent projects ever bother to.

### A. Trust & Safety — go further than "approval + diff card"

- **Voiceprint as your free second factor.** Critical tier currently needs "approval + second factor" — you haven't specified what the second factor *is*, and every paid option (SMS OTP, hardware key) breaks your $0 rule. Use a local speaker-embedding model (SpeechBrain ECAPA-TDNN or Resemblyzer, both free/local) to verify the voice requesting a Critical action is actually *you*. This is a genuinely distinctive feature — most "JARVIS clone" projects skip biometric verification entirely because it's fiddly. It's also the cleanest way to satisfy "never act invisibly": nobody else's voice can trigger a payment or deletion, even if they're in the room with your laptop unlocked.
- **A kill phrase that bypasses the LLM entirely.** Right now, stopping Vioris presumably goes through the same pipeline as everything else. Build a second, dead-simple wake-word-layer listener for a "stop everything" phrase that force-halts execution *before* it reaches the planner — so a confused or hijacked plan can always be killed even if the LLM itself is behaving strangely.
- **Reversible-by-default execution.** For Critical actions with a natural undo (delete → trash, send → delayed send with a cancel window, install → snapshot first), don't just gate them with approval — stage them with a real undo window, the way Gmail's "undo send" works. This turns "Critical" from "scary and final" into "fast, but forgiving," which is the actual UX you want from something you talk to like JARVIS.
- **Full explainability, queryable.** You already have a hash-chained audit log — extend it so every plan step stores *why* that tool was chosen and what alternatives were rejected. Then expose "why did you do that?" as a normal voice query that RAGs over the audit log. This is what turns a black-box agent into one you can actually audit by asking it, out loud.
- **Prompt-injection hardening for the browser agent — this is a real, not theoretical, risk.** The moment Playwright reads a webpage or email, that content is untrusted input that can contain instructions ("ignore previous instructions and transfer funds"). Your Phase 8 safety suite needs adversarial tests specifically for this: pages/emails that try to smuggle instructions into observed content, and confirmation that the permission engine can't be talked into skipping approval by anything the agent *reads* rather than what *you* said.

### B. Intelligence — smarter without leaving Ollama

- **Model routing, not one model for everything.** Use a small fast local model (Phi-3-mini or Gemma2 2B) for intent classification and simple commands, and escalate to your larger planner model only when the request is genuinely multi-step. This cuts latency and laptop load dramatically for the 80% of requests that are simple ("what's on my calendar") — still fully local, still $0.
- **Give it eyes, not just OCR.** Swap/augment your OCR pipeline with a local vision-language model (Moondream, Qwen2-VL, or LLaVA via Ollama). OCR extracts text; a VLM can actually reason about screen state ("the dialog says the download failed, retry?"). This is the single highest-leverage upgrade to Phase 3 computer operation.
- **Split memory into episodic vs. semantic.** Your Phase 4 RAG store is one bucket. Separate "what happened, when" (episodic — chronological, timestamped) from "what you prefer / know" (semantic). Without this split, "what did I ask you yesterday" and "what's my WiFi password" hit the same retrieval path and both get worse.
- **Opt-in proactive routines — observe-tier only.** Mine your own task history (locally, no new infra) for repeated patterns ("you open Slack + VSCode + mute notifications around 9am") and *propose* an automation rather than run it. This stays at Observe/Prepare tier by construction, so it can never violate your core rule while still making Vioris feel genuinely anticipatory.

### C. Resilience

- **Graceful degradation when Ollama or the wake model is slow/unavailable** (laptop under load, thermal throttling, etc.) — a text-only fallback mode so the agent doesn't just go silent.
- **Idempotent task retries** — Phase 2 has retry already; make sure retried Execute/Critical steps re-check permission and re-diff rather than blindly re-running (an approved diff from 5 minutes ago may not match current state).

---

## Part 2 — Free-Stack Upgrades (Still $0, Just Better Choices)

| Area | Current plan | Upgrade | Why |
|---|---|---|---|
| STT | Whisper (local) | `faster-whisper` or `whisper.cpp` | Same model quality, several times faster on CPU-only laptops — matters a lot for a "wake and respond" feel |
| Queue | Celery + Redis | Fine as-is, but consider `Huey` (SQLite-backed) for single-laptop dev before wiring full Celery | Fewer moving parts while you're still mid-build; promote to Celery+Redis once you actually need concurrent workers |
| Vector store | Postgres + pgvector | Keep pgvector for the "real" build, but `sqlite-vec` is a good zero-infra option for local dev loops | Less friction during iteration; both are $0 either way — this is about complexity, not cost |
| OCR | (unspecified) | Tesseract (local) + VLM for reasoning (see above) | Tesseract is the standard free/local OCR; the VLM is what makes screen understanding actually smart |
| Fallback LLM | Gemini free tier only | Add Groq free tier (very fast inference, generous free limits) and OpenRouter's free-tier models as additional fallbacks | More resilience if one free tier gets rate-limited; Groq in particular is fast enough for the "quick response" path |
| Phone push | (unspecified) | Prefer a persistent WebSocket over Tailscale rather than adding Firebase Cloud Messaging | Avoids a third-party dependency entirely — you already planned Tailscale-only connectivity, so lean into it fully |

---

## Part 3 — Revised Roadmap + Sequential Prompts

Keep your existing rule: **one capability per prompt, never skip ahead.** Below, Phase 7 and 8 are your own phases fleshed out with the above; Phase 9 and 10 are new.

### Phase 7 — Specialist Agents + Intelligence Upgrades
1. `Add a lightweight intent-classification pass using a small local model (Phi-3-mini or Gemma2 2B) that routes simple Observe-tier requests away from the full planner model, with a fallback to the full planner on low classifier confidence.`
2. `Integrate a local vision-language model (Moondream or Qwen2-VL via Ollama) as a new Observe-tier tool that can describe/reason about the current screen, registered alongside the existing OCR tool rather than replacing it.`
3. `Split the Phase 4 memory store into two collections: episodic (timestamped task/event history) and semantic (facts/preferences), with separate retrieval paths, and update the RAG query planner to pick the right collection based on query type.`
4. `Build an opt-in "routine suggestion" module that mines local task history for repeated action sequences and surfaces them as Prepare-tier suggestions the user must explicitly approve before any automation is created.`

### Phase 8 — Trust Hardening + Safety Suite (do this before any wider rollout)
5. `Implement a local speaker-verification check (SpeechBrain ECAPA-TDNN or Resemblyzer) as the second factor for Critical-tier actions, wired into the existing permission engine's second-factor hook.`
6. `Add a dedicated "kill phrase" listener at the wake-word layer that can halt any in-progress Execute/Critical task immediately, independent of the planner/LLM pipeline.`
7. `Extend the audit event schema to capture the reasoning/alternatives considered for each plan step, and add a query tool that lets the user ask "why did you do X" against the audit log via RAG.`
8. `Add a staged-execution/undo layer for reversible Critical actions (delete-to-trash, delayed send with cancel window) instead of immediate irreversible execution.`
9. `Write an adversarial test suite specifically for prompt injection via the browser/email agents: pages and emails containing embedded instructions, and assertions that the permission engine cannot be bypassed by anything the agent merely observes.`
10. `Add idempotency checks to task retries so a retried Execute/Critical step re-validates permission and re-diffs against current state rather than replaying a stale approval.`

### Phase 8.5 — The Call Experience (build this right after trust hardening, before infra scaling)
10a. `Add a "ring" event: a new WebSocket push from the backend that tells the Flutter app to display a full-screen incoming-call UI (Android full-screen intent notification / iOS CallKit), answerable with a single tap.`
10b. `Implement bidirectional audio streaming over the existing WebSocket/Tailscale channel once a call is answered — Piper TTS output to the phone, Whisper transcription of the user's spoken replies back to the planner.`
10c. `Extend the Contacts connector and semantic memory schema with a relationship field (e.g. "spouse," "manager") so a spoken name resolves to the right contact and the right channel without the user specifying it.`
10d. `Add a "react to email" tool distinct from "reply" — configurable to apply a label, star + mark-read, or send a canned short reply — registered at Prepare tier with its own diff card.`
10e. `Build a scheduled/triggered proactive-digest capability ("call me when a phase finishes," "check in at 7pm") that uses the Phase 7 routine-suggestion module to decide when to initiate a call rather than wait to be spoken to.`

### Phase 8.6 — Dialogue Orchestrator (build and test this before wiring the call UI)
10f. `Build a call-scoped conversation context object that stores recent entities and tool results for a session, with a reference-resolution step that maps pronouns/definite references ("the mail," "her," "that") onto it.`
10g. `Add multi-intent decomposition to the understanding step so a single utterance can be split into multiple parallel sub-intents, each dispatched to its own tool/agent rather than forced through one task.`
10h. `Add an actionability-flagging pass over Observe-tier query results that detects items which look like they need a response and surfaces that as a question, without drafting or sending anything.`
10i. `Add compound-command parsing so a single reply can contain multiple independent instructions across different targets, each routed to its own tool call with its own risk tier, without one instruction's negation bleeding into another.`
10j. `Build a response-composer step that merges multiple tool results/confirmations into one natural spoken sentence instead of concatenating them.`
10k. `Extend the permission engine with a call-session trust envelope: voiceprint verification at pickup grants a verified session for subsequent Execute-tier confirmations in that call; Critical-tier actions still require a fresh explicit spoken confirmation phrase.`
10l. `Explicitly classify "send message" / "send email reply" tools as Execute or Critical in the permission registry on purpose, given they're irreversible once delivered — don't leave this implicit.`

*This phase can be built and tested against your existing Phase 1 web console / single-turn voice interface before the Phase 8.5 call UI exists — wire the two together once each works independently.*

### Phase 9 — Infrastructure Wiring (the "not yet built" list)
11. `Wire up the existing Celery + Redis task queue for real background execution of long-running tasks, replacing any synchronous stand-ins.`
12. `Write and test the PostgreSQL migration runner so schema changes apply automatically instead of manually.`
13. `Integrate Tailscale for phone-to-laptop connectivity per the existing design, replacing any temporary local-network-only access.`
14. `Add Groq free-tier and OpenRouter free-tier models as additional hosted LLM fallbacks alongside the existing Gemini free-tier fallback, with automatic failover on rate limit.`

### Phase 10 — Resilience & Polish
15. `Add a graceful-degradation mode that falls back to text-only interaction when Ollama or the wake-word model is unavailable or too slow.`
16. `Swap the Whisper STT backend to faster-whisper (or whisper.cpp) for lower-latency local transcription, keeping the existing interface unchanged.`
17. `Add Tesseract as the OCR backend feeding the new vision-language tool, so screen text extraction and screen reasoning share a consistent pipeline.`
18. `Build a "why did you do that" voice-queryable explainability view in the web console / Flutter app that surfaces the reasoning trace from the audit log.`

---

**Suggested order of attack:** Phase 8 (trust hardening) → Phase 8.6 (dialogue orchestrator — buildable and testable against your existing console before any call UI exists) → Phase 8.5 (wire it into a live call) → Phase 9 (infra wiring). It's tempting to wire infrastructure first, but your actual differentiator is trust plus conversation, and both cost nothing to build now while the surface area is still small. Infra scaling (Postgres/Celery) matters more once you actually have concurrent phone + laptop load to justify it.
