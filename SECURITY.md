# Security Policy

## Supported Versions

Only the latest released version of **keel** is supported with security updates.

| Version          | Supported |
| ---------------- | --------- |
| Latest release   | Yes       |
| Earlier releases | No        |

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
single runtime dependency (PyYAML). It sends **no telemetry**. Its outbound network calls
are listed under [Outbound network calls](#outbound-network-calls) below.

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

Security audit reports are published under [`docs/security/`](docs/security/). Each
one names the release line it reviewed and, where the report records it, what produced
it. Three of the five were written by AI models acting as the reviewing security
engineer, as the reports themselves state.

- **2026-09** — no report. The security fixes from that month's review rounds are listed
  under `### Security` in the [CHANGELOG](CHANGELOG.md): 1.23.1 (#1219, #1223 — `keel merge`
  merges the head it checked, and the capture landing resolves refs exactly and goes only
  through a configured remote) and 1.24.0 (#1247 — a host-scoped remote-endpoint opt-in,
  the capture-commit exemption confined to the base repository, `keel gc` through a
  symlink, inspection commands running checkout code).
- [2026-08-15](docs/security/2026-08-15-security-audit.md) — v1.14.2 line; produced by
  Google Antigravity (Gemini 3.7 Flash). It is centred on the swarm subsystem, with a re-check of core invariants (redaction, the remote-endpoint gate, ReDoS, the merge lock), and reports no critical, high or
  medium finding. Read it with that scope in mind: swarm is experimental
  and its live path has never worked end to end
  ([#1281](https://github.com/berkayturanci/keel/issues/1281)), so the report says nothing
  about a swarm run that lands work. It reports no `bandit` or `pip-audit` run.
- [2026-06-15](docs/security/2026-06-15-security-audit.md) — v1.3.0 line; produced by
  Claude (Opus 4.8). Focus: the new `keel-visual` and `website/` surfaces; no critical,
  high or medium finding.
- [2026-06-11](docs/security/2026-06-11-security-audit.md) — v1.2.1 line; produced by
  Claude (Fable 5). No critical, high or medium finding; prior follow-ups verified
  resolved.
- [2026-06-09](docs/security/2026-06-09-security-audit.md) — v1.0.1 line; the report does
  not record what produced it. Secret scanning and consumer-neutrality follow-ups
  (resolved).
- [2026-06-08](docs/security/2026-06-08-security-audit.md) — initial audit (v0.6.1 source
  state); the report does not record what produced it. Trust boundaries, `bandit`,
  `pip-audit`, workflow and permission review.

## Outbound network calls

keel makes a network call only when a command you run needs one:

- **GitHub**, through your authenticated `gh`, when a command reads or writes a pull
  request or issue — a live `ship` / `merge`, `post-comment`, `review`, `capture-land` and
  `swarm-land` among them. When `gh` is not authenticated, keel only reports the
  `github-mcp` capability; any MCP calls are made by the host agent, not by keel
  ([`docs/keel/github-transport.md`](docs/keel/github-transport.md)).
- **Your git remote**, when a lesson is landed: `keel capture-land --write` fetches the
  pull request's branch and pushes the lesson commit onto it.
- **PyPI**, for `keel doctor`'s latest-release check. Skip it with `--offline`.
- **Hosted-API delegates**, when you choose one. `anthropic-api:`, `openai-api:` and
  `google-api:` send the delegate's brief — the prompt file, which can carry the issue
  text and the diff — to `api.anthropic.com`, `api.openai.com` and
  `generativelanguage.googleapis.com`; an `openai-compatible` profile sends it to the
  endpoint the profile names (a non-loopback endpoint needs `KEEL_ALLOW_REMOTE_ENDPOINT`).
- **The `jury` gate**, when `jury` is listed in `gates:` and ai-jury is installed: keel runs
  the `jury` CLI on the change's diff, and ai-jury sends that diff to the reviewer
  providers it is configured with.
- **The gate commands and agent CLIs you configure**, which reach out exactly as you set
  them.

A local Ollama is reached on loopback only (`127.0.0.1:11434`). The optional companion
`keel-visual` looks up issue and pull-request titles through `gh`, and its 3D pages load
three.js (r128, SRI-pinned) from `cdnjs.cloudflare.com` in your browser when they open.

## Telemetry

keel collects and transmits **no telemetry** of any kind — no analytics, no usage
reporting, no phone-home. Its network calls are the ones listed above.
