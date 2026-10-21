"""Tests for the repo-level ``scripts/check_pr_evidence.py`` PR guardrail.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_pr_evidence.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_pr_evidence", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_pr_evidence = _load_script()


class UiFilesTests(TestCase):
    def test_templates_css_and_frontend_source_are_ui(self) -> None:
        files = [
            "laserforce_simulator/templates/leagues/dashboard.html",
            "laserforce_simulator/assets/site.css",
            "frontend/src/app/league/league.component.ts",
            "frontend/src/styles.scss",
        ]
        self.assertEqual(check_pr_evidence.ui_files(files), files)

    def test_python_docs_and_frontend_specs_are_not_ui(self) -> None:
        files = [
            "laserforce_simulator/matches/views.py",
            "docs/self-hosting.md",
            "frontend/src/app/league/league.component.spec.ts",
            ".github/workflows/ci.yml",
        ]
        self.assertEqual(check_pr_evidence.ui_files(files), [])


class HasEvidenceTests(TestCase):
    def test_markdown_image(self) -> None:
        self.assertTrue(check_pr_evidence.has_evidence("![after](shot.png)"))

    def test_html_img_and_video_tags(self) -> None:
        self.assertTrue(check_pr_evidence.has_evidence('<img src="a.png" width=400>'))
        self.assertTrue(check_pr_evidence.has_evidence("<video src=x.mp4></video>"))

    def test_github_attachment_url(self) -> None:
        body = "https://github.com/user-attachments/assets/0b5f7c1e-1234"
        self.assertTrue(check_pr_evidence.has_evidence(body))

    def test_media_link_with_raw_query(self) -> None:
        body = "[flow](https://github.com/o/r/blob/pr-assets/b/flow.webm?raw=true)"
        self.assertTrue(check_pr_evidence.has_evidence(body))

    def test_plain_text_is_not_evidence(self) -> None:
        self.assertFalse(check_pr_evidence.has_evidence("Looks good, see the diff."))
        self.assertFalse(check_pr_evidence.has_evidence(""))

    def test_template_placeholder_comment_is_not_evidence(self) -> None:
        body = "<!-- ![example](screenshot.png) -->\n- "
        self.assertFalse(check_pr_evidence.has_evidence(body))


class OptOutTests(TestCase):
    def test_opt_out_with_reason(self) -> None:
        body = "No visual change: only reorders a queryset behind the table."
        self.assertEqual(
            check_pr_evidence.opt_out_reason(body),
            "only reorders a queryset behind the table.",
        )

    def test_opt_out_without_reason_does_not_count(self) -> None:
        self.assertIsNone(check_pr_evidence.opt_out_reason("No visual change:   "))

    def test_opt_out_inside_comment_does_not_count(self) -> None:
        body = "<!-- No visual change: <why> -->"
        self.assertIsNone(check_pr_evidence.opt_out_reason(body))


class CheckTests(TestCase):
    UI = ["laserforce_simulator/templates/base.html"]

    def test_no_ui_change_passes(self) -> None:
        ok, _ = check_pr_evidence.check(["README.md"], "")
        self.assertTrue(ok)

    def test_ui_change_with_screenshot_passes(self) -> None:
        ok, _ = check_pr_evidence.check(self.UI, "![after](a.png)")
        self.assertTrue(ok)

    def test_ui_change_with_opt_out_passes(self) -> None:
        ok, message = check_pr_evidence.check(
            self.UI, "No visual change: CSS comment only"
        )
        self.assertTrue(ok)
        self.assertIn("CSS comment only", message)

    def test_ui_change_without_evidence_fails_and_names_the_files(self) -> None:
        ok, message = check_pr_evidence.check(self.UI, "Fixes the nav.")
        self.assertFalse(ok)
        self.assertIn("laserforce_simulator/templates/base.html", message)
        self.assertIn("No visual change:", message)
