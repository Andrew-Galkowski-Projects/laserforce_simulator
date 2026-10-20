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


def _function_local_imports(module_file: str) -> set[str]:
    """The module names imported inside a function body of a source file."""
    tree = ast.parse(Path(module_file).read_text(encoding="utf-8"))
    local: set[str] = set()
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(func):
                if isinstance(node, ast.ImportFrom):
                    local.add(node.module or "")
                elif isinstance(node, ast.Import):
                    local.update(alias.name for alias in node.names)
    return local


class TestTournamentApiViewsHaveNoImportCycle(SimpleTestCase):
    """ANG-01f review (S5): the API errors live in a leaf module, the mixins
    import it at module top level, and the detail service never reaches back
    into the serializers."""

    def _package_dir(self) -> Path:
        import matches.tournament_api_views as package

        return Path(package.__file__).parent

    def test_errors_is_a_leaf_module(self) -> None:
        imported = _imported_names(str(self._package_dir() / "errors.py"))
        self.assertFalse(
            [name for name in imported if "tournament_api_views" in name], imported
        )

    def test_no_function_local_import_in_the_api_package(self) -> None:
        offenders = {
            source.name: local
            for source in sorted(self._package_dir().glob("*.py"))
            if (local := _function_local_imports(str(source)))
        }
        self.assertEqual(offenders, {})

    def test_detail_service_does_not_import_the_serializers(self) -> None:
        import matches.tournament_services.detail as detail

        imported = _imported_names(detail.__file__)
        self.assertFalse(
            [name for name in imported if name.startswith("matches.serializers")],
            imported,
        )
        self.assertFalse(_function_local_imports(detail.__file__))


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
