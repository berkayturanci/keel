---
name: keel-onboard
description: Add keel to a repository by running the one-command setup flow, then verify config, adapters, and the rendered plan.
---

# keel-onboard

Use this skill when the user asks to add keel to a project, install keel command adapters,
set up `/keel:<command>` for Claude, create the shared `keel-<command>` skills for other
agents, or migrate a repository onto keel without copying command bodies.

## Workflow

1. Work in a dedicated git worktree or otherwise confirm the active checkout is safe for
   generated file changes.
2. Confirm the `keel` CLI is available:

   ```bash
   keel version
   ```

   If it is missing, install a pinned release:

   ```bash
   pip install keel-workflow
   ```

3. Run the one-command setup from the target repository root:

   ```bash
   keel setup --root .
   ```

   Use `--wizard` when the project should choose base branch, build command, timezone, or
   merge window interactively. Use `--adapter-target claude` or `--adapter-target skills`
   only when the user wants one surface. Use `--force` only when intentionally replacing
   existing generated config and adapter files; it still must not delete or rewrite
   `.keel/extensions/*`.

4. Review `.keel/project.yaml` and keep project-specific behavior in config, policy packs,
   or `.keel/extensions/`. Do not copy or fork the packaged `/keel:<command>` bodies.

5. Run the deterministic verification checks:

   ```bash
   keel validate .keel/project.yaml --root .
   keel plan .keel/project.yaml --root .
   keel adapter-status all --root .
   ```

6. Commit the generated config and adapter files in a normal PR. When keel is later upgraded,
   upgrade the installed package first, then sync generated adapters:

   ```bash
   pipx upgrade keel-workflow
   # or: python -m pip install --upgrade keel-workflow
   keel sync --root .
   keel validate .keel/project.yaml --root .
   keel plan .keel/project.yaml --root .
   ```

## Notes

- `keel setup` is the preferred onboarding command because it wraps `init`,
  `install-adapter`, strict `validate`, and `plan`.
- `keel sync` uses the installed keel package; it does not download the latest PyPI package.
- Existing `.keel/project.yaml` files are reused unless `--force` is supplied.
- Generated adapter files are safe to refresh; project-owned extensions are separate and
  should remain owned by the consumer project.
