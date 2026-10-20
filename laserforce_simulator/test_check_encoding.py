"""Tests for the repo-level ``scripts/check_encoding.py`` guardrail.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_encoding.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_encoding", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_encoding = _load_script()


class CheckFileTests(TestCase):
    def _write(self, data: bytes, suffix: str = ".py") -> str:
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        handle.write(data)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_clean_utf8_file_passes(self) -> None:
        path = self._write('"""An em-dash — is fine."""\n'.encode("utf-8"))
        self.assertEqual(check_encoding.check_file(path), [])

    def test_leading_bom_is_reported_on_line_one(self) -> None:
        # PowerShell 5.1 ``Set-Content -Encoding utf8`` and its native-command
        # pipe both prepend one; a BOM is valid UTF-8, so it slipped through.
        path = self._write(b"\xef\xbb\xbfimport os\n")
        self.assertEqual(check_encoding.check_file(path), [f"{path}:1: UTF-8 BOM"])

    def test_bom_in_frontend_source_is_reported(self) -> None:
        # ANG-04: PowerShell-written Angular source (.ts / .scss) gets a BOM
        # too, so main() must not skip those suffixes.
        for suffix in (".ts", ".scss"):
            with self.subTest(suffix=suffix):
                path = self._write(b"\xef\xbb\xbfexport const x = 1;\n", suffix)
                with redirect_stdout(io.StringIO()) as out:
                    status = check_encoding.main([path])
                self.assertEqual(status, 1)
                self.assertIn(f"{path}:1: UTF-8 BOM", out.getvalue())

    def test_mojibake_is_still_reported(self) -> None:
        path = self._write("x = 'â€”'\n".encode("utf-8"))  # check-encoding: ignore
        problems = check_encoding.check_file(path)
        self.assertEqual(len(problems), 1)
        self.assertIn("mojibake", problems[0])
