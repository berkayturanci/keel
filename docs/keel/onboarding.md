# Consumer onboarding

Use this guide to add keel to a project without copying command bodies into that project.
The project owns only `.keel/project.yaml`, optional `.keel/extensions/`, and generated
adapter files.

## Prerequisites

- Python 3.11 or newer.
- A clean git worktree created from the project's normal base branch.
- The `keel` CLI installed from PyPI, TestPyPI, or a pinned git tag.

```bash
pip install keel-workflow
keel version
```

## One-command setup

Run the setup command from the consumer repository root:

```bash
keel setup --root .
```

`keel setup` performs the first-run sequence:

| phase | purpose |
|---|---|
| `init` | scaffolds `.keel/project.yaml` from detected stack defaults when missing |
| `install-adapter` | installs `/keel:<command>` for Claude and `keel-<command>` skills for other agents |
| `validate` | strict-validates config and extension references |
| `plan` | renders the resolved backbone before any live workflow run |

Use `--wizard` when the default base branch, build command, timezone, or merge window should
be chosen interactively:

```bash
keel setup --root . --wizard
```

Use `--adapter-target claude` or `--adapter-target skills` when only one discovery surface
should be installed. The default is `all`.

Use `--force` only when intentionally regenerating the config and generated adapters from
the installed keel package:

```bash
keel setup --root . --force
```

## After setup

Review `.keel/project.yaml` and replace generic defaults with project policy values:

- `repo` and `base_branch`;
- `knobs.build_gate_cmd` and `knobs.lint_cmd`;
- `timezone` and `merge_window`;
- `knobs.tier3_globs`;
- optional `policy_pack` entries for labels, paths, health providers, and project commands;
- optional extension slots under `.keel/extensions/`.

Then run the deterministic checks:

```bash
keel validate .keel/project.yaml --root .
keel plan .keel/project.yaml --root .
keel adapter-status all --root .
```

Commit the generated config and adapters in a normal PR. Consumer projects should update
generated adapters with `keel update-adapter all --root .` when the pinned keel version
changes; project-owned extensions remain separate and should not be overwritten by adapter
updates.
