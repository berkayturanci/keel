# Operator consent

Operator consent is the pre-work safety gate for live mutating keel workflows. It is
consumer-neutral: keel describes mutation classes, not a private project, credential store,
or hosting provider.

## Consent scopes

Keel maps declared live side effects into these approval scopes:

| scope | examples |
|---|---|
| `filesystem` | writing reports, captures, recaps, or local queue files |
| `git` | creating branches or worktrees, committing, pushing |
| `github` | opening or editing issues/PRs, comments, labels, reviews, merges, closes |
| `secrets` | accessing credentials or secret material for the current run |
| `release` | publishing packages or releases |
| `production-adjacent` | calling configured production-adjacent services |

Read-only diagnostics do not require consent unless they also request secrets or
production-adjacent access.

## Enforcement boundary

Keel core only **emits** the operator-consent contract; it never performs the live
mutation itself. The deterministic core stays read-only, so actual **enforcement depends
on the adapter** (or orchestrator) honoring the emitted contract before it acts. Core can
fail closed on its own preflight (refusing to proceed when required scopes are unapproved),
but it cannot police a downstream agent that ignores the contract.

## Contract behavior

Every structured command contract includes `operator_consent`.

Dry-run mode:

- does not require approval
- still exposes `would_require_operator_consent: true` and the live `consent_scope`
- records no consent record

Live mode:

- exits before project gates or mutation when required scope is not approved
- records approved scope metadata when all required scopes are approved
- passes only the approved scopes that match the resolved plan to delegated agents
- never records secret values

Example:

```bash
keel plan .keel/project.yaml --command ship --live --json
keel plan .keel/project.yaml --command ship --live \
  --approve-scope filesystem,git,github \
  --operator "$USER" \
  --target "issue #123" \
  --json
```

## Delegated agents

Adapters and orchestrators must pass `operator_consent.delegated_agent_scope` to any
delegated agent before work starts. A delegated agent is noncompliant if it attempts to:

- mutate outside `approved_mutation_scopes`
- access secrets without explicit `secrets` approval for the current run
- publish releases without explicit `release` approval
- call production-adjacent services without explicit `production-adjacent` approval

The orchestrator must block or escalate any scope expansion instead of silently continuing.
