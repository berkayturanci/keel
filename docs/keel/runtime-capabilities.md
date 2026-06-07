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
| `filesystem-write` | The configured root is writable. |
| `worktree` | The runtime has `git` and a writable root. |
| `release-publish` | The operator/runtime explicitly allowed release publishing through `KEEL_RELEASE_PUBLISH=1`. |
| `secret-access` | The operator/runtime explicitly allowed secret or credential access through `KEEL_SECRET_ACCESS=1`. |
| `production-adjacent` | The operator/runtime explicitly allowed production-adjacent service access through `KEEL_PRODUCTION_ADJACENT=1`. |
| `private-setup` | The operator/runtime explicitly confirmed private setup prerequisites through `KEEL_PRIVATE_SETUP=1`. |

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
