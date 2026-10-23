"""Tests for the repo-level ``scripts/check_glossary_avoid.py`` naming check.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_glossary_avoid.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_glossary_avoid", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_glossary_avoid = _load_script()

_GLOSSARY = """\
**Round**:
One 15-minute simulated game.
_Avoid_: "game" (informal), "match" (a Match is the pair)

**Single Round**:
A Round played on its own.
_Avoid_: "standalone round", "detailed round"

**Replay**:
_Avoid_: calling it a "compute tier" or a "fast scores estimator" (deferred).
_Avoid_: a null seed means "predates SIM-07", not "real game".
"""


class AvoidTermsTests(TestCase):
    def test_takes_quoted_multi_word_terms_from_avoid_lines(self) -> None:
        self.assertEqual(
            check_glossary_avoid.avoid_terms(_GLOSSARY),
            [
                "standalone round",
                "detailed round",
                "compute tier",
                "fast scores estimator",
                "real game",
            ],
        )

    def test_skips_single_words_and_terms_with_digits(self) -> None:
        terms = check_glossary_avoid.avoid_terms(_GLOSSARY)
        self.assertNotIn("game", terms)
        self.assertNotIn("predates SIM-07", terms)


_DIFF = """\
diff --git a/laserforce_simulator/matches/filters.py b/laserforce_simulator/matches/filters.py
--- a/laserforce_simulator/matches/filters.py
+++ b/laserforce_simulator/matches/filters.py
@@ -10,0 +11,2 @@ class GameRoundFilterSet
+    # keeps only the standalone Round rows
+    single_round = filters.BooleanFilter()
@@ -40 +42 @@ def helper
-def detailed_round():
+def standaloneRound():
diff --git a/GLOSSARY.md b/GLOSSARY.md
--- a/GLOSSARY.md
+++ b/GLOSSARY.md
@@ -1,0 +2 @@
+_Avoid_: "standalone round"
"""


class FindHitsTests(TestCase):
    def test_flags_added_lines_in_any_case_or_joiner(self) -> None:
        hits = check_glossary_avoid.find_hits(["standalone round"], _DIFF)
        self.assertEqual(
            [(h.path, h.line, h.term) for h in hits],
            [
                ("laserforce_simulator/matches/filters.py", 11, "standalone round"),
                ("laserforce_simulator/matches/filters.py", 42, "standalone round"),
            ],
        )

    def test_ignores_removed_lines(self) -> None:
        hits = check_glossary_avoid.find_hits(["detailed round"], _DIFF)
        self.assertEqual(hits, [])

    def test_ignores_the_glossary_adrs_and_plan(self) -> None:
        diff = (
            "diff --git a/docs/adr/0001-x.md b/docs/adr/0001-x.md\n"
            "+++ b/docs/adr/0001-x.md\n"
            "@@ -0,0 +1 @@\n"
            "+the standalone round was renamed\n"
            "diff --git a/PLAN.md b/PLAN.md\n"
            "+++ b/PLAN.md\n"
            "@@ -0,0 +1 @@\n"
            "+shipped as standalone_round first\n"
        )
        self.assertEqual(check_glossary_avoid.find_hits(["standalone round"], diff), [])

    def test_needs_a_word_start(self) -> None:
        diff = (
            "diff --git a/x.py b/x.py\n"
            "+++ b/x.py\n"
            "@@ -0,0 +1 @@\n"
            "+nonstandalone_round = 1\n"
        )
        self.assertEqual(check_glossary_avoid.find_hits(["standalone round"], diff), [])
