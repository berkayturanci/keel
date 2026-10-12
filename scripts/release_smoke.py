#!/usr/bin/env python3
"""Smoke-test a built or published keel package in a clean virtual environment."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

CONSUMER_SPECIFIC_TERMS = (
    "forbidden-consumer-a",
    "forbidden-consumer-b",
    "firebase",
    "realm",
    "billing",
    "crashlytics",
    "play console",
    "adb",
    "espresso",
    "kover",
    "gradle",
)


# pip's wording when the index does not (yet) list the requested version. Only
# these mean "wait and try again"; any other failure is a real defect (#1526).
NOT_FOUND_MARKERS = (
    "No matching distribution found",
    "Could not find a version that satisfies",
)
INSTALL_ATTEMPTS = 6
INSTALL_INTERVAL_SECONDS = 20


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    """Run a command; on failure print it and its output before raising (#1526).

    With ``capture_output=True`` a ``CalledProcessError`` traceback carries only
    the exit status, so the job log never said why ``pip install`` failed.
    """
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"command failed (exit {exc.returncode}): {' '.join(argv)}", file=sys.stderr)
        if exc.stdout:
            print(f"--- stdout ---\n{exc.stdout}", file=sys.stderr)
        if exc.stderr:
            print(f"--- stderr ---\n{exc.stderr}", file=sys.stderr)
        raise
    return result.stdout


def _is_not_found(exc: subprocess.CalledProcessError) -> bool:
    output = f"{exc.stdout or ''}\n{exc.stderr or ''}"
    return any(marker in output for marker in NOT_FOUND_MARKERS)


def _pip_install(
    pip: Path,
    install_args: list[str],
    *,
    retry: bool,
    attempts: int = INSTALL_ATTEMPTS,
    interval: int = INSTALL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Install, retrying only a published requirement the index cannot find yet.

    A just-uploaded release can be missing from the runner's view of the simple
    index for a while; a local wheel never is, so ``retry`` is False for those.
    """
    if not retry:
        _run([str(pip), "install", *install_args])
        return
    argv = [str(pip), "install", "--no-cache-dir", *install_args]
    for attempt in range(1, attempts + 1):
        try:
            _run(argv)
            return
        except subprocess.CalledProcessError as exc:
            if not _is_not_found(exc):
                raise
        if attempt < attempts:
            print(
                f"index cannot resolve the requirement yet (attempt {attempt}/{attempts}); "
                f"retrying in {interval}s",
                file=sys.stderr,
            )
            sleep(interval)
    raise SystemExit(
        f"TIMEOUT: pip could not find the requirement in {attempts} attempts "
        f"over about {(attempts - 1) * interval}s"
    )


def _venv_bin(root: Path, name: str) -> Path:
    if os.name == "nt":
        return root / "Scripts" / f"{name}.exe"
    return root / "bin" / name


def _find_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("keel_workflow-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(
            f"expected exactly one keel_workflow wheel in {dist_dir}, found {len(wheels)}"
        )
    return wheels[0]


def _install_target(args: argparse.Namespace) -> tuple[list[str], str]:
    pip_args: list[str] = []
    if args.index_url:
        pip_args += ["--index-url", args.index_url]
    for extra in args.extra_index_url:
        pip_args += ["--extra-index-url", extra]
    if args.requirement:
        return pip_args + [args.requirement], args.requirement
    wheel = Path(args.wheel) if args.wheel else _find_wheel(Path(args.dist_dir))
    return pip_args + [str(wheel)], str(wheel)


def _installs_published(args: argparse.Namespace) -> bool:
    """True when the target comes from an index (retry-worthy), not a local file."""
    return bool(args.requirement)


def _adapter_count(python: Path) -> int:
    code = "from keel.install import adapter_names; print(len(adapter_names()))"
    return int(_run([str(python), "-c", code]).strip())


def _assert_skill(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{path} is missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3 or "name:" not in parts[1] or "description:" not in parts[1]:
        raise AssertionError(f"{path} has invalid YAML frontmatter")
    if not parts[2].strip():
        raise AssertionError(f"{path} has an empty body")


def _scan_consumer_terms(root: Path) -> list[str]:
    offenders: list[str] = []
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        for term in CONSUMER_SPECIFIC_TERMS:
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if re.search(pattern, text):
                offenders.append(f"{path.relative_to(root)}: {term}")
    return offenders


def smoke(args: argparse.Namespace) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as env_dir, tempfile.TemporaryDirectory() as project_dir:
        venv = Path(env_dir) / "venv"
        project = Path(project_dir) / "project"
        project.mkdir()
        _run([sys.executable, "-m", "venv", str(venv)])
        python = _venv_bin(venv, "python")
        pip = _venv_bin(venv, "pip")
        keel = _venv_bin(venv, "keel")

        install_args, target = _install_target(args)
        _pip_install(pip, install_args, retry=_installs_published(args))

        help_text = _run([str(keel), "--help"])
        version_text = _run([str(keel), "version"]).strip()
        _run([str(keel), "setup", "--root", str(project)])
        sync_text = _run([str(keel), "sync", "--root", str(project), "--dry-run"])
        sync_text_lower = sync_text.lower()
        if "installed keel" not in sync_text_lower or "not upgraded by sync" not in sync_text_lower:
            raise AssertionError("sync output did not report the installed-package boundary")

        adapter_count = _adapter_count(python)
        skills = sorted((project / ".agents/skills").glob("keel-*/SKILL.md"))
        commands = sorted((project / ".claude/commands/keel").glob("*.md"))
        if len(skills) != adapter_count:
            raise AssertionError(f"expected {adapter_count} skills, found {len(skills)}")
        if len(commands) != adapter_count:
            raise AssertionError(f"expected {adapter_count} Claude commands, found {len(commands)}")
        for skill in skills:
            _assert_skill(skill)
        for command in commands:
            if not command.read_text(encoding="utf-8").strip():
                raise AssertionError(f"{command} has an empty body")

        offenders = _scan_consumer_terms(project)
        if offenders:
            raise AssertionError("consumer-specific terms found:\n" + "\n".join(offenders))

        return {
            "target": target,
            "version": version_text,
            "adapter_count": adapter_count,
            "skills": len(skills),
            "claude_commands": len(commands),
            "help_mentions_keel": "keel" in help_text.lower(),
            "sync_reports_package_boundary": True,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--wheel", help="wheel file to install")
    source.add_argument(
        "--requirement",
        help="requirement spec to install, e.g. keel-workflow==0.7.0",
    )
    parser.add_argument(
        "--dist-dir",
        default="dist",
        help="dist directory used when --wheel is absent",
    )
    parser.add_argument(
        "--index-url",
        default=None,
        help="pip index URL for published-package smoke",
    )
    parser.add_argument(
        "--extra-index-url",
        action="append",
        default=[],
        help="additional pip index URL; repeatable",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = smoke(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
