# keel

keel turns a coding agent into the owner of a piece of work. It drives one GitHub issue
through a fixed sequence of steps — select, branch, implement, CI, review, test, merge,
capture, close — so the result is a merged pull request with recorded evidence, not a
first draft left in a working tree. Every project-specific value (base branch, build and
lint commands, reviewers, merge window) is read from the repository's own
`.keel/project.yaml`; nothing in the plugin is tied to one project.

This folder is the plugin bundle submitted to plugin directories. The full project,
documentation and issue tracker live at <https://github.com/berkayturanci/keel>, and the
site is <https://keel-ship.dev/>.

## What you get

- **Claude Code:** seventeen slash commands, `/keel:ship`, `/keel:pr-loop`,
  `/keel:review-cycle`, `/keel:morning`, `/keel:wrap` and the rest, plus the
  `keel-onboard` skill that sets keel up in a repository.
- **Codex and ChatGPT:** the same seventeen workflows as `keel-<command>` skills
  (`keel-ship`, `keel-pr-loop`, …) plus `keel-onboard`.

## Requirements

The commands and skills are instructions for the agent; the deterministic work is done by
the `keel` command-line tool, which this plugin does **not** install. Install it once
from PyPI, then confirm it is on your `PATH`:

```bash
pipx install keel-workflow   # or: uv tool install keel-workflow / pip install keel-workflow
keel version
```

Homebrew (`brew install berkayturanci/keel/keel`) also works; see the project README
for every option. You also need `git`, and for anything that touches GitHub, the GitHub
CLI `gh` signed in with `gh auth login`. Then run the `keel-onboard` skill, or
`keel setup`, in the repository you want keel to manage.

## What this plugin runs, sends and fetches

The plugin is Markdown instructions plus its manifest, license and logo: no hooks, no MCP
servers, no binaries, and nothing that runs when it is installed. When you invoke a command or skill, the
agent runs these programs on your machine, with your credentials:

- **`keel`** — reads `.keel/project.yaml` and writes run state under `.keel/state/` in
  the repository, and calls `git` and `gh` as described below. Beyond those and the
  opt-in delegates at the end of this list, the only address it contacts is PyPI:
  `keel doctor` asks `https://pypi.org/pypi/keel-workflow/json` for the latest released
  version unless you pass `--offline`.
- **`git`** — creates branches and worktrees, commits, **fetches** from and **pushes**
  branches to your `origin` remote.
- **`pip` / `pipx`** — only the `keel-onboard` skill: when the `keel` CLI is missing it
  runs `pip install keel-workflow`, and to upgrade an installed one it runs
  `pipx upgrade keel-workflow` or `python -m pip install --upgrade keel-workflow`. Each
  contacts PyPI (or your configured package index).
- **`gh`** (GitHub CLI), or an authenticated GitHub MCP server when `gh` is not
  available — reads issues, pull requests and CI runs, and **writes** to GitHub as you:
  opens and edits pull requests, posts review and status comments, adds and removes
  labels, opens issues for findings, and **merges** pull requests when the configured
  merge window and evidence gates allow it. Commands that only report (for example
  `/keel:morning`, `/keel:ci-check`) do not merge. `/keel:ship` and most other
  commands accept `--dry-run`: it pushes nothing, writes nothing to GitHub and merges nothing, but
  it still runs local steps such as the project's gates (`keel run-gates`, which can write
  local build artifacts) and, for `/keel:ship`, the reviewers.
- **Your project's own commands** — the build, lint and test commands, gates and
  extensions named in `.keel/project.yaml`. keel runs what that file lists; review it
  as you would a Makefile.
- **Other coding agents, only if you configure them** — the implementer and reviewer
  seats can be delegated to another agent CLI (`claude`, `codex`, `agy`, a local
  `ollama` model, or a CLI profile you define) and the optional cross-vendor review to
  the `jury` CLI (ai-jury). Each receives the issue, the diff and repository context.
  keel's own HTTP client also sends the prompt to any `vendor: openai-compatible`
  delegate profile in `.keel/project.yaml` and any `api` entry in
  `~/.keel/providers.yaml`: the endpoint is loopback by default, and any other host only
  once you set `KEEL_ALLOW_REMOTE_ENDPOINT` (a host list, or `1`); the key is read from
  the env var that profile names. Registry and profile CLI delegates (for example
  `cursor-agent`) are separate programs you install and configure; they send what they
  send. The hosted-API delegates (`anthropic-api:`, `openai-api:`, `google-api:`) read
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY` from your environment and
  send the prompt to that vendor's own API (`api.anthropic.com`, `api.openai.com`,
  `generativelanguage.googleapis.com`); they are off unless you select them and grant
  the `secrets` consent scope.

keel collects no telemetry and sends nothing to its author.

## License

Apache-2.0 — see `LICENSE`.
