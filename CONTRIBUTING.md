# Contributing

## Every new tool, connector, or agent must ship with

- **Input schema** — typed, validated
- **Output schema** — typed, structured
- **Risk classification** — observe / prepare / execute / critical, registered statically (see `docs/03-permissions-and-security.md`)
- **Permission requirements** — exact OAuth scopes or system permissions needed, minimum viable
- **Audit events** — emitted at every state transition
- **Failure behavior** — what happens on timeout, partial failure, and rejection — never a silent no-op
- **Unit tests**
- **Integration tests** — including at least one test that the permission engine actually blocks the action without approval

## Adding a new connector — order of operations

1. Read the service's API terms and confirm automation/personal-account access is actually permitted.
2. Register the connector's tools with their risk levels in the shared permission registry — before writing the connector logic itself.
3. Implement against a mock provider first; real credentials come last.
4. Write the plain-language scope explanation that will show up in the UI's connected-accounts panel.
5. Add the safety tests from `SECURITY.md`'s checklist.

## Code style

- Match existing patterns in `/packages/shared-types` for any new data object.
- Prefer explicit, narrow tool functions over one large multi-purpose tool — narrow tools are what make the permission engine meaningful.
- Every agent (`/agents/*`) only talks to the orchestrator — never directly to another agent.

## PR review

Any PR touching a tool, connector, or agent needs the `SECURITY.md` checklist completed in the PR description before merge.
