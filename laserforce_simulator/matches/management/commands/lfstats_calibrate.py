"""LFS-01 Calibration harness command: sim vs LFstats per Role and metric.

A thin wrapper over ``matches.lfstats_calibration``. The map fixture is loaded
inside a transaction that is always rolled back, so a run leaves nothing in
the database. Output is ASCII only (cp1252 console).
"""

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from matches import lfstats_calibration as cal
from matches.sim_helpers.map_loader import load_map_context
from teams.lfstats_formulas import DEFAULT_CONSTANTS, ImportConstants

REPO_ROOT = Path(settings.BASE_DIR).parent
DEFAULT_TRAIN = REPO_ROOT / "data" / "lfstats_train_2026-10-21.json"
DEFAULT_TEST = REPO_ROOT / "data" / "lfstats_test_internationals_2026.json"
DEFAULT_MAP_FIXTURE = REPO_ROOT / "data" / "maps" / "syracuse.json"

# The search's default grid: three values around each default constant.
DEFAULT_GRID: dict[str, tuple[float, ...]] = {
    "log_scale": (15.0, 25.0, 35.0),
    "accuracy_offset": (-20.0, -10.0, 0.0),
    "accuracy_scale": (80.0, 100.0, 120.0),
    "survival_times_hit_weight": (0.25, 0.5, 0.75),
    "preferred_role_boost": (1.0, 1.1, 1.2),
}


def _flag(field_name: str) -> str:
    return "--" + field_name.replace("_", "-")


class Command(BaseCommand):
    help = (
        "LFS-01 Calibration harness: simulate imported players and compare "
        "with their LFstats numbers per Role and metric."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--split",
            choices=("train", "test", "search"),
            required=True,
            help="train: Training set report; test: replay the Test set; "
            "search: tune the constants on the Training set",
        )
        parser.add_argument(
            "--train",
            default=str(DEFAULT_TRAIN),
            help=f"Training file (default: {DEFAULT_TRAIN.relative_to(REPO_ROOT)})",
        )
        parser.add_argument(
            "--test",
            default=str(DEFAULT_TEST),
            help=f"Test file (default: {DEFAULT_TEST.relative_to(REPO_ROOT)})",
        )
        parser.add_argument(
            "--map-fixture",
            dest="map_fixture",
            default=str(DEFAULT_MAP_FIXTURE),
            help="Map fixture JSON "
            f"(default: {DEFAULT_MAP_FIXTURE.relative_to(REPO_ROOT)})",
        )
        parser.add_argument(
            "--rounds",
            type=int,
            default=20,
            help="Rounds per game (test) or per player (train / search); default 20",
        )
        parser.add_argument(
            "--seed", type=int, default=42, help="RNG seed (default: 42)"
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=1,
            help="Parallel worker processes (default: 1 = serial)",
        )
        for field in fields(ImportConstants):
            parser.add_argument(
                _flag(field.name),
                dest=field.name,
                type=float,
                default=None,
                help=f"ImportConstants.{field.name} "
                f"(default: {getattr(DEFAULT_CONSTANTS, field.name)})",
            )

    def handle(self, *args: Any, **options: Any) -> None:
        split = options["split"]
        constants = replace(
            DEFAULT_CONSTANTS,
            **{
                f.name: options[f.name]
                for f in fields(ImportConstants)
                if options.get(f.name) is not None
            },
        )
        train = _load_json(options["train"], "Training file")
        test = _load_json(options["test"], "Test file") if split == "test" else None
        movement_ctx = _map_context(options["map_fixture"])
        run = {
            "rounds": options["rounds"],
            "seed": options["seed"],
            "workers": options["workers"] or 1,
        }

        try:
            if split == "test":
                report = cal.run_test(
                    train, test, movement_ctx, constants=constants, **run
                )
                self._header(f"Test set, {_describe(constants)}", run)
                self._cells(report.cells)
                self._coverage(report)
            elif split == "train":
                train_report = cal.run_train(
                    train, movement_ctx, constants=constants, **run
                )
                self._header(f"Training set, {_describe(constants)}", run)
                self._cells(train_report.cells)
                self.stdout.write(f"\nObjective: {train_report.objective:.4f}\n")
            else:
                result = cal.search(
                    train,
                    movement_ctx,
                    start=constants,
                    grid=DEFAULT_GRID,
                    on_step=self._progress,
                    **run,
                )
                best = cal.run_train(
                    train, movement_ctx, constants=result.constants, **run
                )
                self._header("Search on the Training set", run)
                self._cells(best.cells)
                self._search(constants, result)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

    # --- output -----------------------------------------------------------

    def _progress(self, step: cal.SearchStep) -> None:
        """One line per scored candidate, flushed: a search can run for hours."""
        what = "start" if step.field is None else f"{step.field}={step.value:g}"
        self.stdout.write(
            f"step pass {step.pass_index} {what} objective {step.objective:.4f}"
            f"{' kept' if step.kept else ''}\n"
        )
        self.stdout.flush()

    def _header(self, title: str, run: dict) -> None:
        self.stdout.write(
            f"\nLFS-01 calibration: {title} "
            f"(rounds {run['rounds']}, seed {run['seed']}, workers {run['workers']})\n"
        )

    def _cells(self, cells: list[cal.CellResult]) -> None:
        for position in cal.POSITIONS:
            rows = [c for c in cells if c.position == position]
            if not rows:
                continue
            self.stdout.write(f"\n{position} (n={rows[0].n_scorecards})\n")
            self.stdout.write(
                f"{'Metric':<22} {'Sim':>10} {'Real':>10} {'Diff':>10} "
                f"{'Pass':>5} {'Rho':>6}\n"
            )
            self.stdout.write("-" * 68 + "\n")
            for c in rows:
                rho = "-" if c.spearman is None else f"{c.spearman:.2f}"
                verdict = "PASS" if c.passed else "FAIL"
                style = self.style.SUCCESS if c.passed else self.style.ERROR
                self.stdout.write(
                    f"{c.metric:<22} {c.sim_mean:>10.3f} {c.real_mean:>10.3f} "
                    f"{c.sim_mean - c.real_mean:>+10.3f} "
                    + style(f"{verdict:>5}")
                    + f" {rho:>6}\n"
                )
        passed = sum(c.passed for c in cells)
        self.stdout.write(f"\nCells passed: {passed} / {len(cells)}\n")

    def _coverage(self, report: cal.TestReport) -> None:
        cov, match = report.coverage, report.match
        self.stdout.write(
            f"Coverage: {cov.used} of {cov.total} games used, "
            f"{cov.skipped_lineup} skipped (lineup), "
            f"{cov.skipped_thin} skipped (thin data, under "
            f"{cal.TEST_MIN_POSITION_GAMES} Training games at the position)\n"
        )
        self.stdout.write(
            f"Match level: {match.games} games, winner agreement "
            f"{match.winner_agreement * 100:.1f}%, mean margin error "
            f"{match.mean_margin_error:.0f}\n"
        )

    def _search(self, start: ImportConstants, result: cal.SearchResult) -> None:
        self.stdout.write(f"\n{'Constant':<28} {'Start':>10} {'Chosen':>10}\n")
        self.stdout.write("-" * 50 + "\n")
        for field in fields(ImportConstants):
            self.stdout.write(
                f"{field.name:<28} {getattr(start, field.name):>10g} "
                f"{getattr(result.constants, field.name):>10g}\n"
            )
        self.stdout.write(f"\nObjective: {result.objective:.4f}\n")
        self.stdout.write(
            f"\n{'Pass':>4} {'Constant':<28} {'Value':>10} {'Objective':>10} Kept\n"
        )
        self.stdout.write("-" * 60 + "\n")
        for step in result.history:
            name = step.field or "(start)"
            value = "-" if step.value is None else f"{step.value:g}"
            kept = "yes" if step.kept else ""
            self.stdout.write(
                f"{step.pass_index:>4} {name:<28} {value:>10} "
                f"{step.objective:>10.4f} {kept}\n"
            )


def _describe(constants: ImportConstants) -> str:
    if constants == DEFAULT_CONSTANTS:
        return "default constants"
    changed = [
        f"{f.name}={getattr(constants, f.name):g}"
        for f in fields(ImportConstants)
        if getattr(constants, f.name) != getattr(DEFAULT_CONSTANTS, f.name)
    ]
    return ", ".join(changed)


def _load_json(path: str, label: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise CommandError(f"{label} not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def _map_context(path: str) -> Any:
    """The fixture's ``MapContext``, built in a transaction that is rolled back."""
    if not Path(path).is_file():
        raise CommandError(f"Map fixture not found: {path}")
    with transaction.atomic():
        try:
            arena_map = cal.load_map_fixture(path)
            movement_ctx = load_map_context(arena_map)[0]
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            transaction.set_rollback(True)
    return movement_ctx
