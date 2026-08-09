# 04 — Feature Catalog

Every feature below is tagged with its default permission tier: **[O]**bserve, **[P]**repare, **[E]**xecute, **[C]**ritical. Use this list directly as your backlog — one feature ≈ one ticket.

## A. Wake, Voice & Presence

- [O] Wake phrase ("Hey Vioris") + configurable alternates
- [O] Two-clap + phrase pattern wake
- [O] Physical desk button / keyboard hotkey wake
- [O] Phone push-to-talk / smartwatch tap wake
- [C] Duress/private wake phrase — silently limits Vioris to Observe-only, no matter what's asked
- [O] Multilingual + code-switch support (e.g., Kannada-English, Hindi-English)
- [O] Speaker recognition — only your voice can trigger Execute/Critical actions
- [O] Emotion/urgency-aware tone shift in replies
- [O] Whisper-quiet / text-only reply mode
- [O] Interruptible speech ("stop talking" mid-sentence)
- [O] Configurable personality presets: Professional / Friendly / Concise / Teacher / Emergency / Silent

## B. Computer Operation

- [O] Open/close/switch applications, multi-monitor aware
- [O] Read visible screen content, describe what's on screen
- [E] Fill web forms
- [E] Move/rename/organize files (allow-listed directories only)
- [O] Search files by content or metadata
- [O] Resume last-used project + uncommitted-change summary
- [E] Run approved terminal commands (allowlist)
- [C] Install new software
- [O] Monitor long-running processes, notify on completion/failure
- [O] Read logs and explain error messages in plain language
- [E] Edit documents/spreadsheets/presentations/code
- [O] Capture screenshots/screen recordings
- [C] Lock, sleep, restart, or shut down the computer
- [O] Live "what Vioris is doing" screen-preview panel with pause/stop always visible

## C. Communication

- [O] Read email + approved messages across accounts
- [O] Cross-platform unified inbox/chat digest (email, WhatsApp, Instagram DMs)
- [O] Urgency/importance classification, not just chronological
- [O] Extract dates, asks, attachments, amounts from messages
- [P] Draft replies in your tone, per-contact style matching
- [O] Translate messages
- [O] Track unanswered conversations, follow-up reminders
- [O] Switch between approved accounts on command
- [E] Send a message (only after approval)
- [P] Prepare a call — contact lookup + talking-point brief
- [E] Start a call (Twilio or native dialer handoff)
- [O] Live call transcription + note-taking during a call
- [P] Auto-drafted follow-up message after a call
- [E] Delegated AI call on your behalf (must disclose it's an AI, cannot make binding commitments)
- [O] "Remind me every N hours until I reply" recurring nudge

## D. Work & Development

- [O] Explain code in a file/selection
- [P] Generate or modify files, staged as a diff
- [E] Run tests
- [O] Investigate a failing test/error and summarize root cause
- [P] Create a branch
- [E] Commit (only after approval; never auto-push to remote without approval)
- [O] Summarize a pull request's changes
- [O] Monitor a deployment/build pipeline, notify on status change
- [O] Watch server logs for anomalies
- [P] Generate documentation / release notes from commit history
- [O] Maintain per-project memory (what you were doing, open questions, decisions made)
- [E] Run a repeatable, pre-approved dev workflow (e.g., "run my pre-commit checklist")

## E. Research & Information

- [O] Web search with source citation
- [O] Compare products/services across sources
- [O] Monitor a topic, send updates on change
- [O] Track a price, alert on drop
- [O] Summarize an article or video
- [O] Extract structured facts from a document
- [P] Compile a report from multiple sources
- [O] Flag conflicting information across sources instead of picking one silently
- [O] Maintain a "research folder" per topic, retrievable later
- [O] Scheduled recurring briefings on a topic

## F. Life Administration

- [E] Book a restaurant/appointment (diff card required)
- [E] Plan and book travel
- [O] Find routes / estimate travel time
- [O] Track a delivery
- [O] Manage reminders and to-dos
- [P] Prepare a form (e.g., a college submission) for your review
- [O] Maintain shopping lists
- [O] Compare subscriptions, flag unused ones
- [O] Monitor bills, alert before due dates
- [P] Prepare an expense report from tracked spending
- [O] Track warranties and renewal dates
- [E] Create/manage a recurring routine ("every Sunday, prep my week")

## G. Personal Intelligence & Memory

- [O] Daily morning briefing (calendar + weather + urgent messages + deadlines)
- [O] Evening wrap-up (what got done, what's open)
- [O] Calendar and deadline awareness across sources (email, LMS, manual)
- [O] Personal knowledge search over indexed notes/PDFs/docs, source-cited
- [O] Contact & relationship context ("who is X, what have we discussed before")
- [O] Routine/pattern detection with an offer to automate
- [O] Follow-up tracking across conversations and tasks
- [O] Priority management / daily focus suggestion
- [O] Decision history ("what did I decide about X last week")
- [O] Personal goal / habit tracking (non-health-diagnostic — tracking only)

## H. Phone Command Center

- [O] Push-to-talk from anywhere
- [O] Live task timeline + approval queue
- [O] Full laptop screen mirroring
- [E] Remote keyboard and mouse control
- [C] Remote lock
- [C] Remote shutdown
- [O] Device/battery/network/storage status
- [O] Searchable, exportable audit history
- [O] Secure file transfer between phone and laptop
- [O] Quick-action shortcuts ("summarize messages," "open my project," "start focus mode")

## I. Smart Environment (stretch)

- [E] Lights, fans, AC, music, TV/streaming control
- [O] Camera/door-sensor status checks
- [E] Printer job management
- [O] Network device / bandwidth monitoring
- [O] Energy usage tracking
- [O] Location-based triggers ("when I reach campus, remind me about the form")
- [O] Phone-camera vision mode — point at a whiteboard/document/error and ask about it
- **Never:** unlocking doors, disabling alarms/security systems, or any safety-critical change from voice alone

## J. Productivity Modes

- [E] Focus Mode — blocks distracting sites/apps, allows only an approved list, for a timed block
- [O] Multi-agent parallel research — spin up sub-agents for independent lookups, merge results
- [O] "Digital twin" scheduling brief — represent your calendar constraints to someone else without exposing full calendar detail

## K. Trust, Safety & Control Surface (the actual differentiator)

- [O] Full activity timeline, every action timestamped
- [O] Approval queue with diff cards
- [O] Memory manager — view, correct, delete any stored fact, with source shown
- [O] Connected-accounts panel — see exact scopes granted per service
- [C] One-tap "disconnect this integration"
- [C] One-tap "delete this memory category"
- [C] One-tap "revoke this device"
- [O] Exportable, hash-verifiable audit log
- [O] Emergency stop — halts any in-flight task tree, target <2s
- [O] Configurable proactivity level: Silent / Normal / Highly Proactive
