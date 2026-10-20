"""ANG-01f-1 — the Tournament service package sits below the view layer.

The League playoffs screen takes the bracket-rounds builder from
``matches.tournament_services.detail`` and never from a view module, and no
``tournament_services`` module imports a view module, so the dependency only
points one way (views -> services).
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase


def _imported_names(module_file: str) -> set[str]:
    """Every module name (and ``module.name``) a source file imports."""
    tree = ast.parse(Path(module_file).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{a.name}" for a in node.names)
    return imported


class TestLeagueScreenServicesBoundary(SimpleTestCase):
    def test_does_not_import_tournament_views(self) -> None:
        import matches.league_screen_services as services

        imported = _imported_names(services.__file__)
        self.assertFalse(
            [name for name in imported if "tournament_views" in name], imported
        )


class TestTournamentServicesBoundary(SimpleTestCase):
    def test_no_service_module_imports_a_view_module(self) -> None:
        import matches.tournament_services as package

        package_dir = Path(package.__file__).parent
        offenders: dict[str, list[str]] = {}
        for source in sorted(package_dir.glob("*.py")):
            bad = [
                name
                for name in _imported_names(str(source))
                if any("views" in part for part in name.split("."))
            ]
            if bad:
                offenders[source.name] = bad
        self.assertEqual(offenders, {})
