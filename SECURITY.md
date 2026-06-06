# Security Policy

## Supported Versions

Only the latest released version of **keel** is supported with security updates.

| Version  | Supported |
| -------- | --------- |
| >= 0.5.0 | Yes       |
| < 0.5.0  | No        |

## Reporting a Vulnerability

Please do **not** open a public issue for security vulnerabilities. Report them privately:

- Email: [berkayturanci@gmail.com](mailto:berkayturanci@gmail.com)
- Or use GitHub's private **“Report a vulnerability”** advisory on the repository.

Include a clear description, reproduction steps, potential impact, and any suggested fix.
We aim to acknowledge within 48 hours when possible.

## Security Notes

keel is a workflow core. The deterministic `keel` CLI (`validate`, `plan`, `run-gates`,
`window`, `ship`, `init`, `install-adapter`) only reads your `.keel/project.yaml` +
extensions and runs the **gate commands you configured** through a thin subprocess wrapper.
It performs **no** network calls of its own and ships a single runtime dependency (PyYAML).

Be aware that:

- **Gate commands run your shell.** `run-gates` / `ship` execute the `build`/`lint`/command
  Lego you put in your config. Review a config before running it on a sensitive repository,
  exactly as you would a Makefile or CI script.
- **The agentic `/keel:<command>` adapters drive coding agents.** When you run them, your
  agent CLI may receive PR diffs and repository context and may push branches, comment, and
  merge. Their reach is governed by your agent's own auth and permissions, not by keel.
- **The optional `jury` gate** invokes the separate
  [ai-jury](https://github.com/berkayturanci/ai-jury) CLI on the change's diff when it is
  installed and listed in `gates:`. keel passes only the diff to that tool and takes no
  runtime dependency on it; when `jury` is absent the gate is a fail-soft no-op.

## Telemetry

keel collects and transmits **no telemetry** of any kind — no analytics, no usage
reporting, no phone-home. The deterministic core makes no network requests; the only
outbound activity comes from the gate commands and agent CLIs you explicitly configure.
