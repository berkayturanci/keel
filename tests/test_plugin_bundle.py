"""The directory-submission bundles stay small, self-contained and in step with the root.

The repository root is the marketplace plugin (``"source": "./"``), so a directory that
installs "the plugin folder" would install the whole repository: well over 500 files,
dozens of binaries, several files past 256 KiB. The Claude plugin directory holds a
submission like that for manual review, and OpenAI's takes a ZIP whose portable format
reads skills from a root ``skills/`` only. ``scripts/plugin_bundle.py`` builds both from
the root sources — the committed ``plugin/`` folder and the ``dist/`` ZIP.

What can rot is pinned here: ``plugin/`` drifting from the commands it copies, a file
that would trip a directory's automated checks (a symlink, a binary, an oversized file,
a ``.DS_Store``, a frontmatter ``description`` that is not a string, a README too short
to describe the plugin), a listing field past its length limit, a logo that is not
square, and a ZIP carrying what the portable format cannot load or a directory refuses
(``.claude-plugin/``, ``commands/``, hooks).

``scripts/`` is maintenance tooling outside the coverage gate, so these tests are what
hold it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import sys
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from keel import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin_bundle = _load("plugin_bundle")

BUNDLE = REPO_ROOT / plugin_bundle.BUNDLE_DIR
#: Claude plugin directory pre-submission checklist: past these, a submission is held
#: for manual review (claude.com/docs/plugins/pre-submission-checklist).
MAX_FILES = 512
MAX_TEXT_BYTES = 256 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
#: The checklist counts README prose only; words inside code blocks do not count.
MIN_README_WORDS = 40
#: OpenAI's listing limits for the ``com.openai`` interface block.
INTERFACE_LIMITS = {
    "displayName": 30,
    "shortDescription": 30,
    "longDescription": 4000,
    "developerName": 80,
}
MIN_ICON_PX = 48
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
#: Shapes of credentials that must never ship in a bundle.
SECRET_SHAPES = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def _frontmatter(text: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    loaded = yaml.safe_load(match.group(1))
    return loaded if isinstance(loaded, dict) else {}


def _prose_words(markdown: str) -> int:
    without_fences = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    without_inline = re.sub(r"`[^`]*`", " ", without_fences)
    return len(re.findall(r"[A-Za-z][A-Za-z'-]*", without_inline))


def _svg_side(data: bytes) -> tuple[float, float]:
    root = ET.fromstring(data)
    view_box = [float(part) for part in root.attrib["viewBox"].replace(",", " ").split()]
    return view_box[2], view_box[3]


def _build_zip(root: Path = REPO_ROOT) -> dict[str, bytes]:
    with TemporaryDirectory() as tmp:
        target = plugin_bundle.build_zip(root, Path(tmp) / "bundle.zip")
        with zipfile.ZipFile(target) as archive:
            return {name: archive.read(name) for name in archive.namelist()}


class TheClaudeBundleIsInSync(unittest.TestCase):
    def test_plugin_folder_matches_its_sources(self):
        problems = plugin_bundle.drift(REPO_ROOT)

        self.assertEqual([], problems, "run `make plugin` to re-sync plugin/")

    def test_it_copies_every_root_command_and_skill(self):
        """Vacuity: a bundle with no commands would also be "in sync"."""
        commands = sorted(p.name for p in (REPO_ROOT / "commands").glob("*.md"))
        bundled = sorted(p.name for p in (BUNDLE / "commands").glob("*.md"))

        self.assertTrue(commands)
        self.assertEqual(commands, bundled)
        self.assertTrue((BUNDLE / "skills" / "keel-onboard" / "SKILL.md").is_file())

    def test_the_manifest_is_the_root_manifest(self):
        root = (REPO_ROOT / ".claude-plugin" / "plugin.json").read_bytes()

        self.assertEqual(root, (BUNDLE / ".claude-plugin" / "plugin.json").read_bytes())

    def test_it_ships_no_hooks_and_no_marketplace(self):
        """`hooks/session-start.sh` bootstraps this repository's own cloud sessions; it is
        not part of the published plugin, and a bundle with hooks cannot go to OpenAI."""
        self.assertFalse((BUNDLE / "hooks").exists())
        self.assertFalse((BUNDLE / ".claude-plugin" / "marketplace.json").exists())


class TheClaudeBundlePassesTheDirectoryChecks(unittest.TestCase):
    def _files(self) -> list[Path]:
        return sorted(p for p in BUNDLE.rglob("*") if not p.is_dir())

    def test_no_symlinks_or_dotfiles_that_block_a_submission(self):
        for path in [BUNDLE, *BUNDLE.rglob("*")]:
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                self.assertFalse(path.is_symlink(), "symlinks are refused")
                self.assertNotEqual(path.name, ".DS_Store")
                self.assertNotEqual(path.name, ".gitmodules")

    def test_file_count_size_and_binary_limits(self):
        files = self._files()

        self.assertLessEqual(len(files), MAX_FILES)
        for path in files:
            rel = path.relative_to(REPO_ROOT).as_posix()
            data = path.read_bytes()
            with self.subTest(path=rel):
                if path.suffix.lower() not in IMAGE_SUFFIXES:
                    self.assertLessEqual(len(data), MAX_TEXT_BYTES)
                self.assertNotIn(b"\0", data, "binary file")
                data.decode("utf-8")

    def test_no_credentials_in_any_file(self):
        for path in self._files():
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                self.assertIsNone(SECRET_SHAPES.search(path.read_text(encoding="utf-8")))

    def test_readme_describes_the_plugin_in_prose(self):
        readme = (BUNDLE / "README.md").read_text(encoding="utf-8")

        self.assertGreaterEqual(_prose_words(readme), MIN_README_WORDS)

    def test_readme_discloses_what_the_plugin_runs_and_sends(self):
        """The directory's security scan compares the README with what the plugin does."""
        readme = (BUNDLE / "README.md").read_text(encoding="utf-8")

        for disclosed in (
            "`gh`",
            "`git`",
            "pushes",
            "merges",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "api.openai.com",
            "generativelanguage.googleapis.com",
            "`jury`",
            "openai-compatible",
            "providers.yaml",
            "KEEL_ALLOW_REMOTE_ENDPOINT",
            "pypi.org",
            "pip",
            "pipx upgrade keel-workflow",
            "pip install --upgrade keel-workflow",
        ):
            with self.subTest(disclosed=disclosed):
                self.assertIn(disclosed, readme)

    def test_license_file_and_field(self):
        manifest = json.loads((BUNDLE / ".claude-plugin" / "plugin.json").read_text("utf-8"))

        self.assertTrue((BUNDLE / "LICENSE").is_file())
        self.assertEqual("Apache-2.0", manifest.get("license"))

    def test_manifest_paths_stay_inside_the_folder(self):
        manifest = json.loads((BUNDLE / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        for key in ("skills", "commands", "agents", "hooks", "mcpServers"):
            value = manifest.get(key)
            if not isinstance(value, str):
                continue
            with self.subTest(key=key):
                resolved = (BUNDLE / value).resolve()
                self.assertTrue(resolved.is_relative_to(BUNDLE.resolve()), value)
                self.assertTrue(resolved.exists(), value)

    def test_every_command_and_skill_has_a_string_description(self):
        files = sorted((BUNDLE / "commands").glob("*.md")) + sorted(
            (BUNDLE / "skills").glob("*/SKILL.md")
        )
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                description = _frontmatter(path.read_text(encoding="utf-8")).get("description")
                self.assertIsInstance(description, str)
                self.assertTrue(description.strip())

    def test_logo_is_square_and_large_enough(self):
        width, height = _svg_side((BUNDLE / "assets" / "logo.svg").read_bytes())

        self.assertEqual(width, height)
        self.assertGreaterEqual(width, MIN_ICON_PX)


class ThePortableManifestDescribesTheSamePlugin(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (REPO_ROOT / plugin_bundle.PORTABLE_MANIFEST).read_text(encoding="utf-8")
        )
        self.interface = self.manifest["extensions"]["com.openai"]["interface"]

    def test_schema_and_required_fields(self):
        self.assertEqual(
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", self.manifest["$schema"]
        )
        for key in ("name", "version", "description", "author", "license", "repository"):
            with self.subTest(key=key):
                self.assertTrue(self.manifest.get(key))
        self.assertRegex(self.manifest["version"], SEMVER)
        self.assertEqual({"name", "url"}, set(self.manifest["author"]))

    def test_identity_matches_the_claude_manifest(self):
        claude = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        for key in ("name", "version", "description", "author", "homepage", "repository"):
            with self.subTest(key=key):
                self.assertEqual(claude[key], self.manifest[key])
        self.assertEqual(claude["license"], self.manifest["license"])

    def test_versions_move_in_lockstep(self):
        for rel in (plugin_bundle.PORTABLE_MANIFEST, "plugin/.claude-plugin/plugin.json"):
            with self.subTest(manifest=rel):
                version = json.loads((REPO_ROOT / rel).read_text("utf-8"))["version"]
                self.assertEqual(__version__, version)

    def test_listing_fields_fit_their_limits(self):
        for key, limit in INTERFACE_LIMITS.items():
            with self.subTest(key=key):
                value = self.interface[key]
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())
                self.assertLessEqual(len(value), limit)
        prompts = self.interface.get("defaultPrompt", [])
        self.assertLessEqual(len(prompts), 3)
        for prompt in prompts:
            self.assertLessEqual(len(prompt), 128)

    def test_icons_point_at_square_assets_the_zip_carries(self):
        entries = plugin_bundle.zip_entries(REPO_ROOT)
        for key in ("logo", "composerIcon"):
            with self.subTest(key=key):
                path = self.interface[key]
                self.assertTrue(path.startswith("./assets/"), path)
                archive_path = path.removeprefix("./")
                self.assertIn(archive_path, entries)
                width, height = _svg_side((REPO_ROOT / entries[archive_path]).read_bytes())
                self.assertEqual(width, height)
                self.assertGreaterEqual(width, MIN_ICON_PX)


class TheOpenAIZipCarriesSkillsOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contents = _build_zip()

    def test_layout(self):
        names = set(self.contents)
        agent_skills = sorted(
            p.name for p in (REPO_ROOT / plugin_bundle.AGENT_SKILLS_DIR).glob("keel-*")
        )
        expected_skills = {"keel-onboard", *agent_skills}

        self.assertLessEqual({"plugin.json", "README.md", "LICENSE", "assets/logo.svg"}, names)
        skills = {name.split("/")[1] for name in names if name.startswith("skills/")}
        self.assertEqual(expected_skills, skills)
        self.assertEqual(18, len(skills), "keel-onboard plus the seventeen command skills")
        for name in names:
            with self.subTest(name=name):
                top = name.split("/")[0]
                self.assertIn(top, {"plugin.json", "README.md", "LICENSE", "assets", "skills"})

    def test_nothing_the_portable_format_cannot_load_or_the_directory_refuses(self):
        for name in self.contents:
            with self.subTest(name=name):
                self.assertFalse(name.startswith((".claude-plugin/", "commands/", "hooks/")))
                self.assertFalse(name.endswith((".app.json", ".DS_Store")))

    def test_the_root_manifest_is_the_portable_one(self):
        source = (REPO_ROOT / plugin_bundle.PORTABLE_MANIFEST).read_bytes()

        # The ZIP stores LF whatever the checkout used (a Windows checkout is CRLF).
        self.assertEqual(source.replace(b"\r\n", b"\n"), self.contents["plugin.json"])

    def test_every_skill_names_itself_and_has_a_string_description(self):
        skill_files = [n for n in self.contents if n.endswith("/SKILL.md")]
        self.assertTrue(skill_files)
        for name in skill_files:
            with self.subTest(name=name):
                meta = _frontmatter(self.contents[name].decode("utf-8"))
                self.assertEqual(name.split("/")[1], meta.get("name"))
                self.assertIsInstance(meta.get("description"), str)

    def test_sizes_and_text_only(self):
        self.assertLessEqual(len(self.contents), MAX_FILES)
        for name, data in self.contents.items():
            with self.subTest(name=name):
                self.assertLessEqual(len(data), MAX_TEXT_BYTES)
                self.assertNotIn(b"\0", data)

    def test_a_windows_build_is_the_same_bytes(self):
        """`ZipInfo` takes `create_system` from `sys.platform` when it is constructed: 0
        on win32, 3 elsewhere. Two builds on one machine always agree, so the guard has
        to build as Windows would; without the pin these bytes differ and the 0644 mode
        is read as DOS attributes."""
        with TemporaryDirectory() as tmp:
            here = plugin_bundle.build_zip(REPO_ROOT, Path(tmp) / "a.zip").read_bytes()
            with unittest.mock.patch.object(sys, "platform", "win32"):
                windows = plugin_bundle.build_zip(REPO_ROOT, Path(tmp) / "b.zip").read_bytes()
            with zipfile.ZipFile(io.BytesIO(windows)) as archive:
                infos = archive.infolist()

        self.assertEqual(here, windows)
        for info in infos:
            with self.subTest(name=info.filename):
                self.assertEqual(3, info.create_system)
                self.assertEqual(0o100644, info.external_attr >> 16)


def _fixture_root(root: Path) -> None:
    files = {
        ".claude-plugin/plugin.json": '{"name": "keel", "version": "1.0.0"}\n',
        "LICENSE": "license\n",
        "website/favicon.svg": '<svg viewBox="0 0 64 64"/>\n',
        "commands/ship.md": "---\ndescription: ship\n---\n",
        "skills/keel-onboard/SKILL.md": "---\nname: keel-onboard\ndescription: x\n---\n",
        ".agents/skills/keel-ship/SKILL.md": "---\nname: keel-ship\ndescription: y\n---\n",
        "packaging/openai-plugin/plugin.json": '{"name": "keel", "version": "1.0.0"}\n',
        "plugin/README.md": "readme\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not write_text: on Windows text mode would write CRLF, and the CRLF test
        # below needs a fixture whose line endings it controls.
        path.write_bytes(text.encode("utf-8"))


class SyncCheckAndZipOnAFixtureTree(unittest.TestCase):
    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = plugin_bundle.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_sync_writes_every_copy_and_then_check_passes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)

            self.assertEqual(1, self._run("check", "--root", tmp)[0])
            code, out, _ = self._run("sync", "--root", tmp)

            self.assertEqual(0, code)
            self.assertIn("5 written", out)
            self.assertEqual(0, self._run("--check", "--root", tmp)[0])
            self.assertEqual("1 written, 0 removed", self._sync_again(root, tmp))

    def _sync_again(self, root: Path, tmp: str) -> str:
        (root / "commands" / "ship.md").write_text("---\ndescription: new\n---\n", "utf-8")
        _, out, _ = self._run("sync", "--root", tmp)
        return out.strip().splitlines()[-1].removeprefix("plugin-bundle: ")

    def test_check_names_each_kind_of_drift(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            plugin_bundle.sync(root)
            (root / "plugin" / "commands" / "ship.md").write_text("edited\n", "utf-8")
            (root / "plugin" / "LICENSE").unlink()
            (root / "plugin" / "commands" / "gone.md").write_text("stale\n", "utf-8")

            problems = plugin_bundle.drift(root)

        self.assertEqual(
            [
                "missing: plugin/LICENSE (copy of LICENSE)",
                "differs: plugin/commands/ship.md != commands/ship.md",
                "unexpected: plugin/commands/gone.md (not generated, not hand-written)",
            ],
            problems,
        )

    def test_sync_removes_a_stale_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            plugin_bundle.sync(root)
            (root / "commands" / "ship.md").unlink()

            _, removed = plugin_bundle.sync(root)

            self.assertEqual(["plugin/commands/ship.md"], removed)
            self.assertEqual([], plugin_bundle.drift(root))

    def test_finder_litter_in_the_bundle_is_drift_and_sync_removes_it(self):
        """The Claude directory blocks a `.DS_Store`. Skipping it while listing *sources*
        is right; skipping it while listing the *bundle* hid it from both commands."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            # Source litter where the bundle actually reads: `skills/` is copied whole
            # and `.agents/skills/keel-*` goes into the ZIP whole. (`commands/` would
            # prove nothing — only `commands/*.md` is ever read.)
            (root / "skills" / "keel-onboard" / ".DS_Store").write_bytes(b"\0source")
            (root / ".agents" / "skills" / "keel-ship" / "Thumbs.db").write_bytes(b"\0source")
            plugin_bundle.sync(root)
            self.assertFalse((root / "plugin" / "skills" / "keel-onboard" / ".DS_Store").exists())
            archive_names = set(plugin_bundle.zip_entries(root))
            self.assertNotIn("skills/keel-onboard/.DS_Store", archive_names)
            self.assertNotIn("skills/keel-ship/Thumbs.db", archive_names)

            (root / "plugin" / "commands" / ".DS_Store").write_bytes(b"\0bundle")
            self.assertEqual(
                ["unexpected: plugin/commands/.DS_Store (not generated, not hand-written)"],
                plugin_bundle.drift(root),
            )
            _, removed = plugin_bundle.sync(root)

            self.assertEqual(["plugin/commands/.DS_Store"], removed)
            self.assertEqual([], plugin_bundle.drift(root))

    def test_make_plugin_zip_regenerates_what_the_zip_reads_first(self):
        """The ZIP reads `.agents/skills/keel-*`, which only `make adapters` regenerates
        and `drift` cannot see; without the prerequisite an edited adapter ships stale."""
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        rule = re.search(r"^plugin-zip:(.*)$", makefile, re.M)
        self.assertIsNotNone(rule, "no plugin-zip target")
        self.assertLessEqual({"adapters", "plugin"}, set(rule.group(1).split()))

    def test_zip_reports_two_skills_with_one_name_instead_of_a_traceback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            plugin_bundle.sync(root)
            clash = root / ".agents" / "skills" / "keel-onboard" / "SKILL.md"
            clash.parent.mkdir(parents=True)
            clash.write_bytes(b"---\nname: keel-onboard\ndescription: z\n---\n")

            code, _, err = self._run("zip", "--root", tmp)

            leftovers = (
                sorted(p.name for p in (root / "dist").glob("*"))
                if (root / "dist").exists()
                else []
            )

        self.assertEqual(1, code)
        self.assertIn("two skills write skills/keel-onboard/SKILL.md", err)
        self.assertEqual([], leftovers, "a refused build must leave no archive behind")

    def test_a_symlink_in_the_bundle_is_drift_even_when_it_points_at_good_bytes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            plugin_bundle.sync(root)
            copy = root / "plugin" / "LICENSE"
            copy.unlink()
            copy.symlink_to(root / "LICENSE")
            link = root / "plugin" / "dangling.md"
            link.symlink_to(root / "nowhere.md")

            problems = plugin_bundle.drift(root)
            plugin_bundle.sync(root)
            after = plugin_bundle.drift(root)

        self.assertTrue(any(p.startswith("symlink: plugin/LICENSE") for p in problems), problems)
        self.assertTrue(
            any(p.startswith("symlink: plugin/dangling.md") for p in problems), problems
        )
        self.assertEqual([], after)

    def test_a_symlinked_skill_directory_is_refused_by_the_zip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (elsewhere / "SKILL.md").write_text("---\nname: keel-x\ndescription: z\n---\n", "utf-8")
            (root / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
            (root / ".agents" / "skills" / "keel-x").symlink_to(elsewhere, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "keel-x is a symlink"):
                plugin_bundle.zip_entries(root)

            # An empty target has no file to trip over: the directory itself is refused.
            (root / ".agents" / "skills" / "keel-x").unlink()
            (root / "empty").mkdir()
            (root / ".agents" / "skills" / "keel-x").symlink_to(
                root / "empty", target_is_directory=True
            )
            with self.assertRaisesRegex(ValueError, "keel-x is a symlink"):
                plugin_bundle.zip_entries(root)

    def test_each_fixed_zip_member_must_be_a_regular_file(self):
        for member in (
            "website/favicon.svg",
            "LICENSE",
            "plugin/README.md",
            "packaging/openai-plugin/plugin.json",
        ):
            with self.subTest(member=member), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _fixture_root(root)
                real = root / (member + ".real")
                (root / member).rename(real)
                (root / member).symlink_to(real)

                with self.assertRaisesRegex(ValueError, "is a symlink"):
                    plugin_bundle.zip_entries(root)

    def test_sync_refuses_a_symlinked_source_and_check_names_it(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            real = root / "LICENSE.real"
            (root / "LICENSE").rename(real)
            (root / "LICENSE").symlink_to(real)

            with self.assertRaisesRegex(ValueError, "LICENSE is a symlink"):
                plugin_bundle.sync(root)
            problems = plugin_bundle.drift(root)

        self.assertTrue(any(p.startswith("symlink: LICENSE") for p in problems), problems)

    def test_a_symlinked_skill_source_is_refused_by_the_zip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            skill = root / ".agents" / "skills" / "keel-x" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.symlink_to(root / "LICENSE")

            with self.assertRaisesRegex(ValueError, "symlink"):
                plugin_bundle.zip_entries(root)

    def test_a_missing_readme_is_drift(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            plugin_bundle.sync(root)
            (root / "plugin" / "README.md").unlink()

            self.assertEqual(
                ["missing: plugin/README.md (hand-written)"], plugin_bundle.drift(root)
            )

    def test_zip_refuses_a_drifted_bundle_and_builds_a_synced_one(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)

            code, _, err = self._run("zip", "--root", tmp)
            self.assertEqual(1, code)
            self.assertIn("refusing", err)

            plugin_bundle.sync(root)
            code, out, _ = self._run("zip", "--root", tmp)
            self.assertEqual(0, code)
            target = root / "dist" / "keel-plugin-1.0.0.zip"
            self.assertIn(str(target), out)
            with zipfile.ZipFile(target) as archive:
                names = sorted(archive.namelist())

        self.assertEqual(
            [
                "LICENSE",
                "README.md",
                "assets/logo.svg",
                "plugin.json",
                "skills/keel-onboard/SKILL.md",
                "skills/keel-ship/SKILL.md",
            ],
            names,
        )

    def test_a_crlf_checkout_builds_the_same_lf_zip(self):
        """Windows CI checks out with CRLF; the uploaded ZIP must not depend on that."""
        with TemporaryDirectory() as tmp:
            lf_root, crlf_root = Path(tmp) / "lf", Path(tmp) / "crlf"
            for root in (lf_root, crlf_root):
                _fixture_root(root)
            for path in crlf_root.rglob("*"):
                if path.is_file():
                    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            for root in (lf_root, crlf_root):
                plugin_bundle.sync(root)

            lf = plugin_bundle.build_zip(lf_root, Path(tmp) / "lf.zip").read_bytes()
            crlf = plugin_bundle.build_zip(crlf_root, Path(tmp) / "crlf.zip").read_bytes()

        self.assertEqual(lf, crlf)

    def test_two_skills_with_one_name_are_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture_root(root)
            clash = root / ".agents" / "skills" / "keel-onboard" / "SKILL.md"
            clash.parent.mkdir(parents=True)
            clash.write_text("---\nname: keel-onboard\ndescription: z\n---\n", "utf-8")

            with self.assertRaisesRegex(ValueError, "two skills write"):
                plugin_bundle.zip_entries(root)

    def test_no_action_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            plugin_bundle.main([])


if __name__ == "__main__":
    unittest.main()
