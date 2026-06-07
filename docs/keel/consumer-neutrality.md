# Consumer-neutral core boundary

Keel core is the portable workflow engine. It must stay neutral so one installed package can
drive many different repositories without carrying policy from any one consumer.

## Core owns

Keel core may define generic workflow mechanics:

- the fixed backbone step order and step invariants
- config and extension schema validation
- command parsing and structured plan/result contracts
- runtime capability detection
- GitHub transport abstractions
- worktree safety, merge locks, merge-window handling, and dry-run semantics
- generic review/test/fix-loop budgets and decision vocabulary
- adapter installation and update mechanics

## Projects own

A consumer repository owns all product, stack, and organization policy:

- branch names and merge windows
- build, lint, test, coverage, and audit commands
- path globs and high-risk areas
- labels and status transitions beyond keel's generic vocabulary
- health, telemetry, release, and production-adjacent signals
- manual smoke-test playbooks and one-off local commands
- reviewer rubric additions and domain-specific gates
- credential sources and any approval required to use them

Those values belong in `.keel/project.yaml`, project policy packs, `.keel/extensions/`, or
project-provided commands. They do not belong in packaged keel command bodies.

## Runtime owns

Runtime capability and transport choices are not project policy. They describe what the
current execution environment can do:

- whether `gh` is available and authenticated
- whether a GitHub MCP/API transport is available
- whether shell, browser, subagent, device, or release-publish capabilities are available
- whether a workflow has operator consent for a live mutating run

Commands should ask the runtime for these capabilities and stop or degrade explicitly when a
required capability is missing.

## Adapter rule

Adapters may translate a packaged command into a host-agent surface, but they must not add
consumer policy. A generated adapter file may mention config knob names, extension hook names,
or runtime capability names. It must not mention a consumer product, private repository,
private service, framework-specific gate, path glob, or command that only applies to one
consumer.

## Review rule

When reviewing a keel core change, reject it if the implementation would make another
consumer inherit policy that should have lived in project config or an extension. If a rule is
useful only for one repository, add an extension hook or policy-pack field instead of adding it
to core.
