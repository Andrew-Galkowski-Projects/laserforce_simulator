"""PERF-02b red check: a deliberate failure, reverted in the next commit.

Proves one failing shard turns ``test-result`` red, and that a test missing
from ``.test_durations`` still runs.
"""

from django.test import SimpleTestCase


class Perf02RedCheckTests(SimpleTestCase):
    def test_deliberately_fails(self) -> None:
        self.assertEqual(1, 2, "PERF-02b red check: test-result must go red")
