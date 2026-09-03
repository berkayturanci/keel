# Runtime capabilities

Runtime capabilities describe what the current execution environment can do before keel
starts a workflow. They are separate from project policy: project config can require a
capability, but the detector reports whether this particular run can satisfy it.

## Capability report

Use `keel capabilities` to inspect the current runtime:

```bash
keel capabilities --root .
keel capabilities --root . --json
keel capabilities --project .keel/project.yaml --for ship --root .
```

The report is available in human-readable form and as JSON. JSON output is also embedded in
the structured command contracts described in [`command-contracts.md`](command-contracts.md).

## Built-in capability names

| capability | meaning |
|---|---|
| `shell` | A local shell is available for command gates. |
| `git` | The `git` binary is available. |
| `gh` | The GitHub CLI is available. |
| `gh-auth` | The GitHub CLI reports an authenticated session. |
| `github-mcp` | A host runtime reports GitHub MCP/API access through `KEEL_GITHUB_MCP=1`. |
| `subagents` | A host runtime reports subagent support through `KEEL_SUBAGENTS=1`. |
| `parallel-subagents` | A host runtime reports parallel subagent support through `KEEL_PARALLEL_SUBAGENTS=1`. |
| `browser` | A host runtime reports browser automation support through `KEEL_BROWSER=1`. |
| `adb` | The Android Debug Bridge is available on `PATH`, or the host reports it through `KEEL_ADB=1`. |
| `firebase` | The Firebase CLI is available on `PATH`, or the host reports it through `KEEL_FIREBASE=1`. |
| `filesystem-write` | The configured root is writable. |
| `worktree` | The runtime has `git` and a writable root. |
| `release-publish` | The operator/runtime explicitly allowed release publishing through `KEEL_RELEASE_PUBLISH=1`. |
| `secret-access` | The operator/runtime explicitly allowed secret or credential access through `KEEL_SECRET_ACCESS=1`. |
| `providers` | At least one **tool-capable** implementer is available: an agent CLI (`claude` / `codex` / `agy`) on `PATH`. A hosted-API or local-model delegate does not satisfy it — those run under keel's no-tools contract, where the orchestrator performs every mutation. The detail names the CLIs found. |
| `review-vendors` | At least **2 distinct vendors** are available across all three transports (agent CLIs on `PATH`, local models, hosted keys present), so a cross-vendor review panel is possible here. Two reviewers from one vendor is one opinion twice. The detail names the vendors — never a key value. |
| `api-token` | A hosted-API delegate key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`) is present in the environment for the `anthropic-api:`/`openai-api:`/`google-api:` delegates. The report names the env vars found — never their values; whether the *selected* delegate's own key is present is checked again at dispatch. Reading the key in a live run still requires `secrets` consent. |
| `production-adjacent` | The operator/runtime explicitly allowed production-adjacent service access through `KEEL_PRODUCTION_ADJACENT=1`. |
| `private-setup` | The operator/runtime explicitly confirmed private setup prerequisites through `KEEL_PRIVATE_SETUP=1`. |

A generic CLI delegate (a `knobs.delegate_profiles` entry — see
[`configuration.md`](configuration.md#delegate_profiles)) adds **no new capability**: running
the profile's `command` is the same `shell` subprocess surface the built-in `codex`/`agy`
delegates already use, and it needs no key, so neither `secret-access` nor `api-token`
applies.

`providers` and `review-vendors` are detected **cheaply**: `keel capabilities` runs on every
command, so it only does `PATH` lookups and reads env-var *names*. It never shells out and
never opens a socket. The deep probe is a separate, explicit command.

## Probing providers: `keel doctor --providers`

`keel doctor --providers [--json]` answers the machine-dependent question the capability
report only summarises: **which delegates are usable here, and why not the rest?** It covers
every built-in vendor, every `knobs.delegate_profiles` entry, and every entry of the
machine-level [provider registry](configuration.md#provider-registry):

```bash
keel doctor --providers
keel doctor --providers --json
keel doctor .keel/project.yaml --providers --json
```

Each row reports `available` / `reason` / `transport` (`cli` · `api` · `local`) /
`capabilities` / the models the provider lists for itself:

| field | meaning |
|---|---|
| `tools` | The provider can run the git/PR steps itself. True for `cli` transports only. |
| `read_only_mode` | A documented way to run it without write tools exists — `claude --disallowed-tools`, `codex -s read-only`, `agy --sandbox`, or, for a profile/registry entry, the operator's `review_args`. keel cannot *enforce* read-only for an arbitrary CLI; this reports that the lever exists. A provider with no tools at all reports `false` — read `tools` first. |
| `model_selection` | The caller can choose the model (a model flag for a CLI; the request body for `api`/`local`). |
| `models` | What the provider itself listed: `agy models`, Ollama's `/api/tags`. Empty when it exposes no listing, or the listing failed. |

How each transport is probed:

- **agent CLI** — on `PATH`, *and* answering `<command> --version`. A binary that is present
  but broken would otherwise be reported as a usable implementer and fail at s4, which is the
  expensive place to find out.
- **hosted API** — is the vendor's key **present** in the environment? Names only: the value
  is never read, printed, or sent. No request is made — whether a key *works* is a question
  only the vendor can answer, and asking costs a billable call on every `doctor` run.
- **local (Ollama)** — the `ollama` binary on `PATH` *and* `GET http://127.0.0.1:11434/api/tags`,
  which also yields the served model list. That URL is a hardcoded loopback constant: keel
  never dials an endpoint that a config or registry file names just because `doctor` ran. A
  registry `local` entry is probed by its command alone.

Every probe is **time-boxed** (5 s per subprocess, 3 s for the HTTP call) and **fail-soft**: a
missing binary, a non-zero exit, a timeout, an unreachable server or a malformed answer all
become `available: false` with a reason, never an exception. The whole pass answers in
seconds.

The `providers` check joins the doctor report only under this flag. It is `ok` when at least
one provider is available, `warn` when the registry is malformed or nothing at all is usable,
and `fail` on a registry **name clash** — which `--strict` turns into a non-zero exit.

## Declaring requirements

Project config can declare global requirements:

```yaml
knobs:
  build_gate_cmd: "./tools/build-check"
  required_capabilities: [shell]
  optional_capabilities: [gh, gh-auth]
```

Extensions can declare their own requirements in frontmatter:

```markdown
---
id: extra-report
slot: tester
kind: command
run: ./tools/report
required_capabilities: [shell]
optional_capabilities: [browser]
---
```

Project-provided commands declare the same fields in `policy_pack.project_commands`:

```yaml
policy_pack:
  name: example
  project_commands:
    device-smoke:
      command: ".keel/commands/device-smoke"
      required_capabilities: [shell, adb]
      optional_capabilities: [browser, firebase]
      side_effects: [report_write]
```

Unknown capability names fail validation so typos do not silently degrade a run.

## Required vs optional

Required capabilities fail before mutating work begins. Optional capabilities degrade
explicitly: human output reports `degraded optional`, and JSON output includes
`missing_optional`. Mutating or privileged capability names also extend the operator-consent
scope in structured command contracts; for example, `release-publish` requires `release`
consent and `secret-access` requires `secrets` consent for the current live run.

For example, `keel ship --pr N` requires the selected GitHub transport to support
`check_runs`. Authenticated `gh` supports that operation today; MCP/API hosts can support it
by reporting the normalized transport capability described in
[`github-transport.md`](github-transport.md).

## Boundary

Capability detection must stay consumer-neutral. Keel core may know generic capability
names and environment signals, but project-specific tools, credentials, commands, and
manual procedures belong in project config, extensions, or project-provided commands.
