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

## Risk x trust escalation

The consent block also includes `risk_trust_escalation`, a pure-core decision record for
operator escalation. It does not weaken `requires_operator_consent`; it explains when an
adapter should escalate based on two signals and standard triggers:

- risk tier from `s5 classify`: `tier-1`, `tier-2`, or `tier-3`
- trust signal from the adapter/runtime: `high`, `medium`, or `low`
- triggers: side-effecting action, repeated retry, conflicting sources, and large diff
- deterministic low-risk sampling with a `0..99` sample bucket

Side-effecting or irreversible work is always an operator gate. Tier-3 work gates unless the
trust signal is high; tier-2 work gates on low trust. Unknown risk or trust values fail
closed to tier-3 / low trust. The decision remains emit-only in core: enforcement stays at
the execution layer, and an agent cannot self-approve the escalation.

Consent mode is selected for every run in this order:

1. `--consent-mode explicit|standing|agent`
2. `KEEL_CONSENT_MODE`
3. `consent_mode` in `.keel/project.yaml`
4. built-in `explicit`

Modes:

- `explicit`: live mutation requires `--approve-scope` on that invocation.
- `standing`: trusted unattended approval may come from env or config.
- `agent`: enforcement is delegated to the host agent permission model; keel still emits
  the full contract and delegated scope, but does not fail preflight just because
  `--approve-scope` is absent.

Approval sources inside `standing` mode, in precedence order:

1. `--approve-scope` flags for the current command invocation.
2. `KEEL_APPROVE_SCOPE`, for trusted unattended environments such as cron or CI.
3. `automation.approved_scopes` in `.keel/project.yaml`.

Standing approval only satisfies the consent preflight. It never bypasses findings,
project gates, CI, the merge window, or the merge lock. Scope remains least-privilege:
only listed scopes are approved, and any broader required scope still stops the run.
Use `KEEL_OPERATOR` with `KEEL_APPROVE_SCOPE`, or `automation.operator` with config-based
approval, so the `consent_record` names the automation identity. Standing approval without
an operator identity fails the live preflight. Dry-run and read-only commands ignore
standing approval environment/config values unless an explicit `--approve-scope` flag is
passed.

Example:

```bash
keel plan .keel/project.yaml --command ship --live --json
keel plan .keel/project.yaml --command ship --live \
  --approve-scope filesystem,git,github \
  --operator "$USER" \
  --target "issue #123" \
  --json
KEEL_APPROVE_SCOPE=filesystem,git,github KEEL_OPERATOR=automation:nightly \
  keel overnight .keel/project.yaml --live --consent-mode standing --json
KEEL_CONSENT_MODE=agent \
  keel ship .keel/project.yaml --live --json
```

Config-based standing approval:

```yaml
consent_mode: standing
automation:
  approved_scopes: [filesystem, git, github]
  operator: automation:nightly
```

## Delegated agents

Adapters and orchestrators must pass `operator_consent.delegated_agent_scope` to any
delegated agent before work starts. A delegated agent is noncompliant if it attempts to:

- mutate outside `approved_mutation_scopes`
- access secrets without explicit `secrets` approval for the current run
- publish releases without explicit `release` approval
- call production-adjacent services without explicit `production-adjacent` approval

The orchestrator must block or escalate any scope expansion instead of silently continuing.
