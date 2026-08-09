# 08 — Free Stack & Remote Access

Two hard constraints added to the project: **$0 running cost**, and **phone-to-laptop access from anywhere, without paying for a tunnel/relay service.** This changes several picks from `docs/02-architecture.md`. Treat this doc as overriding that one wherever they conflict.

## 1. Why "free" changes the architecture, not just the tools

Two categories of cost hide in a project like this: **per-call cost** (LLM API tokens, TTS characters, telephony minutes) and **hosting cost** (a cloud server running 24/7). The fix for both is the same direction: **run everything on your own laptop as the "server," and treat your phone as a thin client that reaches it through a free private network.** No cloud orchestration server to pay for, no per-request API metering beyond what a local model costs you in electricity.

## 2. Full Stack Swap Table

| Component | Original pick | Free replacement | Notes |
|---|---|---|---|
| LLM core (planning/reasoning) | Paid Claude/Gemini API at scale | **Local LLM via Ollama** (Llama 3.1 8B / Qwen2.5 / Mistral, whichever runs well on your hardware) | Free forever, runs on your laptop. Trade-off: weaker function-calling reliability than frontier hosted models — see §5 |
| LLM (backup / when local isn't enough) | — | **Gemini API free tier** or **Claude API free credits** for occasional heavier reasoning calls | Free tiers have daily/monthly limits — use only for tasks local models genuinely struggle with, not as the default |
| STT | Whisper (already free) | **Whisper (local, via faster-whisper or whisper.cpp)** | Already free and open-source — no change needed, just run it locally, not via a paid API |
| TTS | ElevenLabs (paid) | **Piper TTS** or **Coqui TTS** (local, open-source) | Noticeably more robotic than ElevenLabs, but zero cost and fully offline |
| Wake word | Porcupine (free tier limited) | **openWakeWord** (fully open-source, unlimited, local) | No account/API key needed at all |
| Telephony (calls) | Twilio (paid per minute) | **Skip in v1** — trigger your phone's native dialer via remote-control instead of a server-side call API | True server-initiated calling always costs money somewhere; defer this feature until you're ready to pay for it deliberately |
| Task queue | Celery + Redis (self-hosted, free) | Same — Redis and Celery are free, self-hosted | No change |
| DB | PostgreSQL + pgvector | Same, self-hosted on the laptop | Free, no change |
| Browser automation | Playwright | Same | Free, open-source, no change |
| Remote access / phone↔laptop tunnel | Custom relay server (would need hosting) | **Tailscale** (free for personal use, up to 100 devices) | This is the key unlock — see §3 |
| Push notifications | FCM/APNs | Same — both are free for this scale of usage | No change |
| Mobile app | Flutter | Same | Free, open-source |
| Hosting for a "gateway" server | A cloud VM (would cost money) | **None needed** — the gateway runs locally on your laptop; Tailscale handles the "reachable from anywhere" part | This removes an entire monthly cost line |

## 3. Remote Access: How "from anywhere" works for $0

**Don't build a custom relay server or open a port on your router.** Both of those either cost money (a always-on cloud relay) or are a real security risk (port-forwarding your laptop directly to the internet). Use **Tailscale** instead:

- Tailscale creates a private, encrypted mesh network (built on WireGuard) between your devices — your laptop and your phone both get a stable private IP that only your devices can reach, from anywhere, over any network.
- Free tier covers up to 100 devices for personal use — you'll use 2.
- No port forwarding, no public IP exposure, no relay server to host or pay for.
- Setup is: install Tailscale on the laptop, install Tailscale on the phone, log into the same account on both, done. Your Flutter app then just talks to `http://<laptop-tailscale-ip>:<port>` as if it were on the same LAN, even when you're across the country.

### Updated flow

```
Phone (Flutter app, on Tailscale network)
        ↓  (WebSocket over the Tailscale private network — no public exposure)
Laptop — runs everything locally:
   FastAPI gateway + orchestrator + task-runner
   Local LLM (Ollama) + local Whisper + local Piper TTS
   Computer agent + browser agent
   Postgres + Redis (both local)
        ↓
Your accounts (via OAuth, same as before — this part doesn't change)
```

This means: **your laptop must be on and connected to the internet for remote access to work** — there's no cloud server keeping Vioris "alive" while your laptop is off. That's an honest trade-off of the free architecture, not a bug: if you later want Vioris reachable even when your laptop is asleep, that requires either leaving the laptop on, or eventually renting a small always-on server for the orchestration layer — a decision to make deliberately later, not something to solve for now.

### Security note (still applies from `docs/03-permissions-and-security.md`)

Tailscale replaces "the custom relay + mTLS" from the original architecture doc, but it doesn't replace the app-level rules: device pairing, JWTs, the permission engine, and the audit log all still apply *on top of* the Tailscale network. Tailscale keeps the network private; your own auth still decides what a paired device is allowed to do once it's on that network.

## 4. Updated `.env.example` additions

```
LLM_PROVIDER=ollama            # or "gemini_free_tier" for occasional fallback
OLLAMA_MODEL=llama3.1:8b
TTS_ENGINE=piper
STT_ENGINE=whisper_local
WAKE_WORD_ENGINE=openwakeword
TAILSCALE_LAPTOP_IP=100.x.x.x  # filled in after Tailscale setup
GATEWAY_PORT=8420
```

## 5. Honest Trade-offs of Going Fully Free

- **Local LLM function-calling is less reliable than frontier hosted models.** Expect to spend more of Phase 2 (planner) time on prompt engineering and a stricter JSON schema/retry loop to get consistent step plans out of a smaller local model. Budget extra testing time here specifically.
- **Local TTS sounds more robotic** than ElevenLabs-tier voices. Fine for a v1; revisit if the experience matters enough to justify a small paid tier later.
- **No server-initiated phone calls without a paid telephony API** — Twilio's free trial is extremely limited. v1 delegated-calling should mean "Vioris drafts the script and opens your phone's dialer for you to tap call," not "Vioris calls on its own," until you're ready to pay for that specific feature.
- **"Anywhere" access depends on your laptop being on.** If that's a real constraint for your use case (e.g., you want briefings even when the laptop's asleep), the cheapest fix later is a tiny always-on box (even a Raspberry Pi on your home network, still reachable via the same free Tailscale network) running just the orchestrator — not a paid cloud VM.

## 6. What Changes in Earlier Docs

- `docs/02-architecture.md` §9 "Suggested Stack" → superseded by §2 of this doc.
- `docs/05-roadmap-and-prompts.md` Phase 1 prompts → when prompting your coding agent, explicitly say "use local Whisper, Piper TTS, and Ollama — not a paid API" so it doesn't default to a hosted provider.
- `docs/05-roadmap-and-prompts.md` Phase 5 (Phone Control Center) → the pairing/gateway prompts should target Tailscale-network reachability, not a custom public relay.
