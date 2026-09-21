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
`window`, `init`, `install-adapter`) only reads your `.keel/project.yaml` + extensions and
runs the **gate commands you configured** through a thin subprocess wrapper, and ships a
single runtime dependency (PyYAML). It sends **no telemetry**. The only outbound activity is
deliberate and named: a live `ship` / `merge` reaches GitHub for the pull request's evidence,
`keel doctor` checks PyPI for the latest release (skip it with `--offline`), and the gate and
agent commands you configure reach out exactly as you set them.

Be aware that:

- **`keel gc` and the gitignore self-heal do not follow a symlink out of the repo.** A committed `.keel/scratch` symlink is refused rather than having its target's contents deleted, and a symlinked `.keel/.gitignore` is not written through. Cleaning `.keel/scratch` unlinks a symlinked child instead of recursing into it.
- **Inspection commands do not run a project's code.** `keel doctor` (documented read-only) does not execute `scripts/find_python.sh` from the inspected checkout, and the provider probe behind `keel doctor --providers` / `plan` / `swarm-plan` refuses a `delegate_profiles.*.command` that is a *relative* path — it would resolve against the working tree. Configure a PATH command or an absolute path. A gate command you configured is still yours to run (below).
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

## Security Audits

Periodic security audit reports are published under
[`docs/security/`](docs/security/). Each report covers source-level trust
boundaries, static analysis (`bandit`), dependency scanning (`pip-audit`),
workflow/permission review, and the repository's GitHub security settings:

- [2026-06-11](docs/security/2026-06-11-security-audit.md) — v1.2.1 line; no findings,
  prior follow-ups verified resolved.
- [2026-06-09](docs/security/2026-06-09-security-audit.md) — v1.0.1 line; secret
  scanning and consumer-neutrality follow-ups (resolved).
- [2026-06-08](docs/security/2026-06-08-security-audit.md)

## Telemetry

keel collects and transmits **no telemetry** of any kind — no analytics, no usage
reporting, no phone-home. The deterministic core makes no network requests; the only
outbound activity comes from the gate commands and agent CLIs you explicitly configure.
