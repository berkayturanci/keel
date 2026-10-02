#!/usr/bin/env python3
"""Build the plugin bundles keel submits to the Claude and OpenAI plugin directories.

The repository root *is* the marketplace plugin (``.claude-plugin/marketplace.json``
says ``"source": "./"``), which is right for ``/plugin install keel`` and wrong for a
directory review: an installer of the root receives the whole repository — the CLI
source, the test suite, the site's GIFs and PNGs, well over 500 files, several of them
past 256 KiB and dozens of them binary. Both directories hold a submission like that.

So the directories get a folder that holds only what an agent loads, built from the
root sources rather than maintained beside them:

``plugin/`` — the Claude plugin directory bundle (committed)
    ``.claude-plugin/plugin.json`` (a byte copy of the root manifest), ``commands/``
    (byte copies of the generated root ``commands/``), ``skills/keel-onboard/``,
    ``LICENSE`` and ``assets/logo.svg`` (the site favicon). ``README.md`` is the one
    hand-written file. No hooks: ``hooks/session-start.sh`` is repository tooling.

``dist/keel-plugin-<version>.zip`` — the OpenAI (ChatGPT + Codex) upload (built, ignored)
    The portable format reads skills from a root ``skills/`` only and has no commands,
    so the ZIP carries ``packaging/openai-plugin/plugin.json`` as its root
    ``plugin.json`` and every keel skill: ``keel-onboard`` plus the seventeen generated
    ``.agents/skills/keel-*``. Putting those seventeen into ``plugin/skills`` instead
    would show every Claude user each workflow twice, once as a command and once as a
    skill, which is why the two bundles differ.

Usage::

    python3 scripts/plugin_bundle.py sync     # rewrite plugin/ from the root sources
    python3 scripts/plugin_bundle.py check    # exit 1 if plugin/ has drifted (also --check)
    python3 scripts/plugin_bundle.py zip      # write dist/keel-plugin-<version>.zip

``make plugin`` runs ``sync`` after regenerating ``commands/``; the drift test in
``tests/test_plugin_bundle.py`` runs ``check``. Stdlib only, like every script here.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

#: The Claude directory bundle: the folder holding ``.claude-plugin/plugin.json``.
BUNDLE_DIR = "plugin"
#: The single hand-written file inside the bundle; everything else is a copy.
HANDWRITTEN = (f"{BUNDLE_DIR}/README.md",)
#: The OpenAI portable manifest, the ZIP's root ``plugin.json``. Versioned with the
#: release surfaces (``scripts/release_surfaces.py``).
PORTABLE_MANIFEST = "packaging/openai-plugin/plugin.json"
#: The generated shared skill set the OpenAI bundle carries beside ``skills/``.
AGENT_SKILLS_DIR = ".agents/skills"
AGENT_SKILLS_GLOB = "keel-*"

#: A fixed timestamp for every ZIP entry, so the same tree always builds the same bytes.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _files_under(root: Path, rel_dir: str) -> list[str]:
    """Every regular file under ``root/rel_dir``, repo-relative, sorted."""
    base = root / rel_dir
    return sorted(
        path.relative_to(root).as_posix()
        for path in base.rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    )


def bundle_copies(root: Path) -> dict[str, str]:
    """The Claude bundle's generated files: ``plugin/<path>`` -> root source path."""
    out = {
        f"{BUNDLE_DIR}/.claude-plugin/plugin.json": ".claude-plugin/plugin.json",
        f"{BUNDLE_DIR}/LICENSE": "LICENSE",
        f"{BUNDLE_DIR}/assets/logo.svg": "website/favicon.svg",
    }
    for source in sorted((root / "commands").glob("*.md")):
        out[f"{BUNDLE_DIR}/commands/{source.name}"] = f"commands/{source.name}"
    for source in _files_under(root, "skills"):
        out[f"{BUNDLE_DIR}/{source}"] = source
    return out


def drift(root: Path) -> list[str]:
    """Every way ``plugin/`` differs from what ``sync`` would write (empty = in sync)."""
    problems: list[str] = []
    expected = bundle_copies(root)
    for dest, source in expected.items():
        dest_path = root / dest
        if not dest_path.is_file():
            problems.append(f"missing: {dest} (copy of {source})")
        elif dest_path.read_bytes() != (root / source).read_bytes():
            problems.append(f"differs: {dest} != {source}")
    allowed = set(expected) | set(HANDWRITTEN)
    for present in _files_under(root, BUNDLE_DIR):
        if present not in allowed:
            problems.append(f"unexpected: {present} (not generated, not hand-written)")
    for handwritten in HANDWRITTEN:
        if not (root / handwritten).is_file():
            problems.append(f"missing: {handwritten} (hand-written)")
    return problems


def sync(root: Path) -> tuple[list[str], list[str]]:
    """Rewrite ``plugin/`` from the root sources. Returns (written, removed)."""
    written: list[str] = []
    removed: list[str] = []
    expected = bundle_copies(root)
    for dest, source in expected.items():
        dest_path = root / dest
        data = (root / source).read_bytes()
        if dest_path.is_file() and dest_path.read_bytes() == data:
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(data)
        written.append(dest)
    allowed = set(expected) | set(HANDWRITTEN)
    for present in _files_under(root, BUNDLE_DIR):
        if present not in allowed:
            (root / present).unlink()
            removed.append(present)
    return written, removed


def portable_version(root: Path) -> str:
    """The version the OpenAI manifest declares (in lockstep with every surface)."""
    manifest = json.loads((root / PORTABLE_MANIFEST).read_text(encoding="utf-8"))
    return str(manifest["version"])


def zip_entries(root: Path) -> dict[str, str]:
    """The OpenAI ZIP's contents: archive path -> repo-relative source path."""
    entries = {
        "plugin.json": PORTABLE_MANIFEST,
        "README.md": f"{BUNDLE_DIR}/README.md",
        "LICENSE": "LICENSE",
        "assets/logo.svg": "website/favicon.svg",
    }
    for source in _files_under(root, "skills"):
        entries[source] = source
    for skill_dir in sorted((root / AGENT_SKILLS_DIR).glob(AGENT_SKILLS_GLOB)):
        for source in _files_under(root, skill_dir.relative_to(root).as_posix()):
            archive = "skills/" + source[len(AGENT_SKILLS_DIR) + 1 :]
            if archive in entries:
                raise ValueError(f"two skills write {archive}: {entries[archive]} and {source}")
            entries[archive] = source
    return entries


def _lf(data: bytes) -> bytes:
    """Every entry is text; store it with LF line endings whatever the checkout used.

    A Windows checkout with ``core.autocrlf`` turns every Markdown file into CRLF, and the
    ZIP would then differ by platform — and carry ``---\r\n`` frontmatter that a strict
    reader does not recognise. Normalising here keeps the upload one artifact per tree.
    """
    return data.replace(b"\r\n", b"\n")


def build_zip(root: Path, out: Path | None = None) -> Path:
    """Write the deterministic OpenAI ZIP (LF line endings) and return its path."""
    target = out or root / "dist" / f"keel-plugin-{portable_version(root)}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in sorted(zip_entries(root).items()):
            info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, _lf((root / source).read_bytes()))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build keel's directory plugin bundles.")
    parser.add_argument("action", nargs="?", choices=("sync", "check", "zip"), default=None)
    parser.add_argument("--check", action="store_true", help="same as the `check` action")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root")
    parser.add_argument("--out", default=None, help="zip: output path (default dist/…)")
    args = parser.parse_args(argv)
    action = "check" if args.check else args.action
    if action is None:
        parser.error("choose an action: sync, check (or --check), zip")
    root = Path(args.root)

    if action == "check":
        problems = drift(root)
        if problems:
            print("plugin/ has drifted from its sources; run `make plugin`:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print("plugin/ is in sync with its sources")
        return 0
    if action == "sync":
        written, removed = sync(root)
        for rel in written:
            print(f"  wrote {rel}")
        for rel in removed:
            print(f"  removed {rel}")
        print(f"plugin-bundle: {len(written)} written, {len(removed)} removed")
        return 0
    problems = drift(root)
    if problems:
        print("refusing to zip a drifted bundle; run `make plugin` first:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    target = build_zip(root, Path(args.out) if args.out else None)
    print(f"plugin-bundle: wrote {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
