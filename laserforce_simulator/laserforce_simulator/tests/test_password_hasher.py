"""PERF-05: tests hash passwords with MD5; production keeps Django's PBKDF2.

Full PBKDF2 on every ``create_user(..., password=...)`` / ``login`` cost about a
third of the suite's summed test time. The root conftest's ``pytest_configure``
swaps in ``MD5PasswordHasher`` for tests only; ``settings.py`` must never set
``PASSWORD_HASHERS``, so production stays on Django's default.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import get_hasher
from django.test import SimpleTestCase, TestCase

import laserforce_simulator.settings as production_settings


class TestPytestUsesMd5(TestCase):
    def test_active_hasher_is_md5(self) -> None:
        self.assertEqual(get_hasher().algorithm, "md5")

    def test_a_new_accounts_password_is_an_md5_hash(self) -> None:
        user = get_user_model().objects.create_user(
            email="perf05-hasher@example.com", password="Str0ng-Passphrase-9"
        )
        self.assertTrue(user.password.startswith("md5$"), user.password)
        self.assertTrue(user.check_password("Str0ng-Passphrase-9"))


class TestProductionSettingsKeepDefaultHasher(SimpleTestCase):
    def test_settings_module_sets_no_password_hashers(self) -> None:
        self.assertFalse(hasattr(production_settings, "PASSWORD_HASHERS"))
