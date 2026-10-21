"""Tests for the repo-level ``scripts/pr_assets.py`` screenshot publisher.

The script lives outside the Django project, so it is loaded by path. The
publish test builds a throwaway origin + clone in a temp dir and drives git
there (``cwd=``), so it never touches this checkout.
"""

import importlib.util
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pr_assets.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pr_assets", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr_assets = _load_script()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class ParseRemoteTests(TestCase):
    def test_https_and_ssh_urls(self) -> None:
        for url in (
            "https://github.com/Owner/repo.git",
            "https://github.com/Owner/repo",
            "git@github.com:Owner/repo.git",
        ):
            with self.subTest(url=url):
                self.assertEqual(pr_assets.parse_remote(url), "Owner/repo")

    def test_non_github_remote_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pr_assets.parse_remote("https://gitlab.com/o/r.git")


class MarkdownTests(TestCase):
    def test_image_is_embedded(self) -> None:
        md = pr_assets.markdown_for("O/r", "fix-nav/after.png")
        self.assertEqual(
            md,
            "![after](https://github.com/O/r/blob/pr-assets/fix-nav/after.png?raw=true)",
        )

    def test_video_is_linked(self) -> None:
        md = pr_assets.markdown_for("O/r", "fix-nav/flow.webm")
        self.assertEqual(
            md,
            "[Video: flow.webm](https://github.com/O/r/blob/pr-assets/fix-nav/flow.webm?raw=true)",
        )

    def test_unsupported_extension_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pr_assets.markdown_for("O/r", "fix-nav/notes.txt")


class AssetPathTests(TestCase):
    def test_branch_slashes_become_dashes(self) -> None:
        self.assertEqual(
            pr_assets.asset_path("fix/uxp-07-mobile", Path("C:/tmp/before.png")),
            "fix-uxp-07-mobile/before.png",
        )


class PublishTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.origin = self.tmp / "origin.git"
        _git(self.tmp, "init", "--bare", "-q", str(self.origin))
        self.clone = self.tmp / "clone"
        _git(self.tmp, "clone", "-q", str(self.origin), str(self.clone))
        _git(self.clone, "config", "user.email", "t@example.com")
        _git(self.clone, "config", "user.name", "T")
        (self.clone / "README.md").write_text("x")
        _git(self.clone, "add", "README.md")
        _git(self.clone, "commit", "-q", "-m", "init")
        _git(self.clone, "push", "-q", "origin", "HEAD:main")
        self.shot = self.tmp / "after.png"
        self.shot.write_bytes(b"PNG fake")

    def test_publish_creates_then_extends_the_assets_branch(self) -> None:
        paths = pr_assets.publish(self.clone, "fix/a", [self.shot])
        self.assertEqual(paths, ["fix-a/after.png"])
        self.assertEqual(
            _git(self.origin, "show", "pr-assets:fix-a/after.png"), "PNG fake"
        )
        second = self.tmp / "flow.webm"
        second.write_bytes(b"webm")
        pr_assets.publish(self.clone, "fix/b", [second])
        listing = _git(self.origin, "ls-tree", "-r", "--name-only", "pr-assets")
        self.assertEqual(listing.split("\n"), ["fix-a/after.png", "fix-b/flow.webm"])

    def test_publish_leaves_the_checkout_untouched(self) -> None:
        before = _git(self.clone, "rev-parse", "HEAD")
        pr_assets.publish(self.clone, "fix/a", [self.shot])
        self.assertEqual(_git(self.clone, "rev-parse", "HEAD"), before)
        self.assertEqual(_git(self.clone, "status", "--porcelain"), "")
