# Installing keel into an agent

The [README's Install section](../../README.md#install) covers the **`keel` CLI** —
Homebrew, the curl installer, pipx/uv. This page covers the other half: getting
keel in front of an agent as a **plugin**, so its commands and skills are
available inside a session.

The two are independent, and the plugin is the one that does **not** require
`pip`. `docs/keel/plugin.md` describes what the plugin contains; this page is
about getting it installed, per agent, and — the part that is not guessable —
keeping it up to date.

Every command below was run against the tooling on a real machine on
**2026-09-09**, with keel **1.22.0**. Where something could not be exercised end
to end, it says so.

## Contents

- [Claude Code](#claude-code)
- [Codex](#codex)
- [Antigravity (`agy`)](#antigravity)
- [Cursor](#cursor)

---

## Claude Code

<a id="claude-code"></a>

This repository doubles as a single-plugin marketplace: the marketplace is added
once and the plugin installed from it.

**Install**

```bash
claude plugin marketplace add https://github.com/berkayturanci/keel
claude plugin install keel@keel
```

In a running session the slash-command equivalents are
`/plugin marketplace add berkayturanci/keel` then `/plugin install keel`.

**Update**

```bash
claude plugin marketplace update keel
claude plugin update keel@keel
```

Two measured details:

- **`claude plugin install` is a no-op on an installed plugin** — it reports that
  the plugin is already installed and changes nothing, so it is not an upgrade
  path.
- **`plugin update` needs the qualified `name@marketplace`.** The bare name fails
  with `Plugin "keel" not found` and exit status 1; `keel@keel` answers
  `✔ keel is already at the latest version (1.22.0).` A restart applies it.

`claude plugin list` shows the installed version and scope.

---

## Codex

<a id="codex"></a>

**Install**

```bash
codex plugin marketplace add https://github.com/berkayturanci/keel
codex plugin add keel@keel
```

Adding the marketplace registers the source; it does not install the plugin.

**Update**

```bash
codex plugin marketplace upgrade
codex plugin add keel@keel
```

`codex plugin marketplace upgrade` is Codex's own description of the refresh —
*"Refresh configured Git marketplace snapshots"* — and `plugin add` then installs
from the refreshed snapshot. `codex plugin list` shows what is installed and from
which marketplace.

`AGENTS.md` is read by Codex without any plugin at all, which is what makes the
CLI route useful in a container or a CI job.

---

## Antigravity

<a id="antigravity"></a>

**Install**

```bash
agy plugin install https://github.com/berkayturanci/keel
agy plugin enable keel
```

`install` alone leaves the plugin **disabled**; the `enable` is not optional.

**Update**

```bash
agy plugin install https://github.com/berkayturanci/keel
```

It overwrites in place and keeps the enabled flag.

**What agy actually reads.** Components are discovered by **root-directory
convention only** — a root `skills/`, and likewise `commands/`, `agents/`,
`mcp_config.json`, `hooks.json`. No manifest path field is consulted. keel's root
ships `skills/` and `commands/` and none of the other three, so those two are what
an install imports.

**`agy plugin list` reports what was imported, not what is on disk.** It records
the component list at import time, so an install made before an upstream layout
change keeps the old answer — on this machine keel reads `"components":
["commands"]` from a 2026-09-08 import, while the checkout beside it carries
`skills/`. Re-run `agy plugin install <url>` after any release that adds a
component directory.

---

## Cursor

<a id="cursor"></a>

**Cursor has no CLI install command.** `cursor-agent plugin` exposes only
`marketplace` (`add`, `list`, `remove`, `update`) — there is no
`cursor-agent plugin install`. Two routes, and they register different things.

**Install — local checkout**

```bash
git clone --depth 1 https://github.com/berkayturanci/keel \
  ~/.cursor/plugins/local/keel
```

Then restart Cursor. It is *reported* to list as `keel (Local)` under
**Settings → Plugins**. That is a GUI claim and has not been confirmed from a CLI
session — everything else on this page was run.

**Update — local checkout**

```bash
git -C ~/.cursor/plugins/local/keel pull
```

Restart Cursor.

**Install — marketplace**

```bash
cursor-agent plugin marketplace add https://github.com/berkayturanci/keel
```

Then install it from Cursor's `/plugins` screen.
`cursor-agent plugin marketplace update <nameOrUrl>` re-indexes the marketplace
from its git repository.

### What each Cursor route registers, measured

A **locally installed** Cursor plugin registers **skills only** — no commands, no
subagents, no MCP servers. That is Cursor's local-plugin behaviour rather than
anything about keel's manifest: a plugin declaring no components at all behaves
the same way.

So keel's 17 `/keel:<command>` entries do not come from a local install. On a
machine where they appear in Cursor, they are being read out of **Claude Code's
plugin cache**:

```
$ ls ~/.claude/plugins/cache/keel/keel/
1.19.3/   1.21.1/   1.22.0/
```

Each of those version directories holds all 17 commands, and only `1.22.0` holds
`skills/` — the root `skills/` directory landed in that release. Commands reached
Cursor through a neighbouring tool's cache, pinned to whichever version directory
Claude happens to have kept, and they would disappear if that directory were
pruned.

If the commands matter in Cursor, the marketplace route is the one that registers
them.

## See also

- [`docs/keel/plugin.md`](plugin.md) — what the plugin contains and how it is generated.
- [`docs/keel/editors.md`](editors.md) — the **VS Code / Cursor editor extension**,
  which is a different product from the agent plugin this page is about: it installs
  with `code --install-extension` and does not give you the skills or the
  `/keel:<command>` set.
- [`README.md`](../../README.md#install) — installing the `keel` CLI itself.
