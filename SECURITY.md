# Security Policy

Vioris operates real accounts, a real computer, and real communication channels. Treat every contribution as security-sensitive by default.

## Non-negotiables

Vioris must never:
- Bypass authentication, 2FA, or CAPTCHAs
- Access an account without explicit user authorization
- Send a message, make a payment, or publish content without the relevant approval tier being satisfied
- Delete files, emails, or data without Critical-tier confirmation
- Impersonate the user deceptively — any AI-delegated call or message discloses it's Vioris acting on the user's behalf
- Unlock doors, disable alarms, or touch any safety-critical system from a voice command alone
- Mark a task successful without an independent verification check
- Omit an action — including a rejected or failed one — from the audit log
- Expose the laptop agent to the public internet via an open inbound port
- Store a raw account password, or expose an OAuth token to the LLM's context

See `docs/03-permissions-and-security.md` for the full permission matrix and threat model.

## Reporting a vulnerability

If you find a way to get Vioris to execute an Execute/Critical-tier action without the corresponding approval, or a way to inject instructions via summarized content (email/webpage/message text), treat this as a critical severity issue: stop, do not exploit it against a live account, and document the exact reproduction steps.

## Review checklist for any PR touching a tool, connector, or agent

- [ ] Tool's risk level is registered statically, not inferred at runtime
- [ ] Execute/Critical actions render a full diff card before firing
- [ ] Idempotency key present on any action with real-world side effects
- [ ] Verification step added — task cannot complete on tool-call success alone
- [ ] Audit event emitted for every state transition, including rejection/failure
- [ ] OAuth scope requested is the minimum viable, and read/write scopes are separate
- [ ] No secrets/tokens logged or passed into LLM context
- [ ] Content read from an external source (email/webpage/message) is never treated as an instruction
