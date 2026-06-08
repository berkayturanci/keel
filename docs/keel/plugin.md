# keel as a Claude Code plugin

keel ships its agentic `/keel:<command>` workflows as a **Claude Code plugin** in addition to
the `pip install keel-workflow` + `keel install-adapter` path. Both flows are additive and use
the same project-neutral command bodies; you do not have to choose one.

## Install (no `pip` required)

This repository is itself a single-plugin **marketplace**, so a user adds it and installs the
plugin in two steps:

```text
/plugin marketplace add berkayturanci/keel   # register the marketplace (this repo)
/plugin install keel                          # install the keel plugin
```

After installing, the workflows are available as namespaced slash commands — `/keel:ship`,
`/keel:regression`, `/keel:review-cycle`, and the rest of the [shipped set](commands.md). The
plugin name (`keel`) is the namespace, so a flat `commands/ship.md` is discovered as
`/keel:ship`.

The CLI equivalents are `claude plugin marketplace add berkayturanci/keel` and
`claude plugin install keel@keel` (the marketplace and plugin are both named `keel`).

## What ships

| File | Role |
|---|---|
| `.claude-plugin/plugin.json` | Plugin manifest — `name: keel`, `version` (matches `keel.__version__`), description, author, `homepage`, `license: Apache-2.0`. Its `version` is kept in lockstep with the package by a test. |
| `.claude-plugin/marketplace.json` | Single-plugin marketplace — `name: keel`, `owner`, one `plugins[]` entry with `source: "./"` (the plugin lives at the repo root). Lets the repo be added via `/plugin marketplace add berkayturanci/keel`. |
| `commands/<cmd>.md` | The plugin command bodies, discovered from the default `commands/` directory. **Generated** from `src/keel/adapters/commands/`; do not hand-edit. |
| `skill/keel-onboard/SKILL.md` | The onboarding skill (referenced by `plugin.json`'s `skills` field). |

The command bodies are the **same** project-neutral adapters used by
`keel install-adapter claude` — they read every project value from `.keel/project.yaml` via
the `keel` CLI, so a project still needs keel configured (`keel setup`) for the flows to act.

## Single source of truth + drift guard

`src/keel/adapters/commands/*.md` is the **only** place the command bodies are authored. The
committed `commands/*.md` files are generated from them, so they are never hand-duplicated:

```bash
make plugin                      # regenerate commands/ from src/keel/adapters/commands/
# equivalently:
keel install-adapter plugin --root .
```

`keel.install.plugin_files()` is the pure generator; `keel install-adapter plugin` (and
`make plugin`) write its output. A test (`tests/test_install.py::TestClaudeCodePlugin`)
asserts the committed `commands/*.md` files are **byte-identical** to the generator output, so
any drift — or a stale file after the source bodies change — fails `make test`. Further tests
lock that `plugin.json`'s `version` equals `keel.__version__` and that both JSON manifests
parse and carry their required fields.

## Relationship to `pip install`

The `pip install keel-workflow` + `keel install-adapter {claude,skills,all}` flow is
unchanged. The plugin is **repo-level** packaging (the command files live at the repo root and
are not pip package-data), so the published wheel is unaffected. Use whichever distribution
fits: the plugin for a quick `/plugin install` in a Claude Code session, or the package when
you also want the `keel` CLI and the shared non-Claude skill surface.
