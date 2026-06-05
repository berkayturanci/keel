# keel CLI reference

```
keel <command> [options]
keel --version
```

## `keel version`

Print the keel version.

## `keel validate <project.yaml…> [--root DIR]`

Validate one or more project configs against the bundled schema. Reports `OK` /
`INVALID` / `MISSING` per file; exits non-zero if any file is invalid.

With `--root DIR`, also **strict-validates the extensions** the config references
(resolved under `DIR/<extensions_dir>`): a missing or malformed extension fails the file.
Without `--root`, only the config schema is checked.

```bash
keel validate projects/*.yaml                 # schema only
keel validate .claude/project.yaml --root .   # schema + extensions (use in CI)
```

## `keel plan <project.yaml> [--root DIR]`

Render the backbone plan for a project: the fixed steps with the project's built-in gates
and extensions slotted in. This is the dry-run view — what an actual run would execute.

`--root DIR` (default `.`) is where extension files are resolved. Extensions that can't be
loaded are reported as warnings on stderr (fail-soft) and the plan still renders with the
built-in gates.

```bash
keel plan .claude/project.yaml
```

Example output:

```
keel plan — ingreview
  base_branch: main   core_version: ^0.1
  backbone:
     s0  config
     ...
     s8  test
           - gate: build
           - gate: lint
           - gate: design-parity
    s10  merge
           - gate: design-parity-gate
    ...
```

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | a config was invalid/missing, or a plan target could not be loaded |
| 2 | no command given (help printed) |
