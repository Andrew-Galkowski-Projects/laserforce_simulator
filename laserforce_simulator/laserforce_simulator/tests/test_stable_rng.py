"""Every test starts from an RNG state fixed by its own name, so a rare simulator
outcome fails the same way on every run and in any order (MECH-07 retro)."""

import random
from unittest import TestCase as PlainTestCase

import pytest
from django.test import TestCase

from laserforce_simulator.testing import stable_seed

_MODULE = "laserforce_simulator/tests/test_stable_rng.py"


def _first_draw(name: str) -> float:
    return random.Random(stable_seed(name)).random()


class TestStableSeed(PlainTestCase):
    def test_the_same_name_gives_the_same_seed(self) -> None:
        self.assertEqual(stable_seed("a::b"), stable_seed("a::b"))

    def test_different_names_give_different_seeds(self) -> None:
        self.assertNotEqual(stable_seed("a::b"), stable_seed("a::c"))

    def test_it_is_pinned_across_processes(self) -> None:
        # str.__hash__ changes per process: this would catch a move to hash().
        self.assertEqual(stable_seed("a::b"), 221770033)


def test_a_test_body_starts_from_its_own_seed(request: pytest.FixtureRequest) -> None:
    assert random.random() == _first_draw(request.node.nodeid)


def test_another_test_body_starts_elsewhere(request: pytest.FixtureRequest) -> None:
    assert random.random() == _first_draw(request.node.nodeid)
    assert _first_draw(request.node.nodeid) != _first_draw("something else")


class TestClassLevelSetup(TestCase):
    """The flake that prompted this ran in ``setUpTestData``, before any
    function-scoped fixture, so class setup needs a seed of its own."""

    first_draw: float

    @classmethod
    def setUpTestData(cls) -> None:
        cls.first_draw = random.random()

    def test_class_setup_starts_from_the_class_seed(self) -> None:
        self.assertEqual(
            self.first_draw, _first_draw(f"{_MODULE}::TestClassLevelSetup")
        )

    def test_the_body_starts_from_its_own_seed(self) -> None:
        nodeid = f"{_MODULE}::TestClassLevelSetup::{self._testMethodName}"
        self.assertEqual(random.random(), _first_draw(nodeid))
