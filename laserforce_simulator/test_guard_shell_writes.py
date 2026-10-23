"""Tests for the repo-level ``scripts/guard_shell_writes.py`` PreToolUse hook.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "guard_shell_writes.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("guard_shell_writes", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_script()

SCRATCH = "C:/Users/me/AppData/Local/Temp/claude/x/scratchpad"


class TestRefusesShellWritesToRepoFiles(TestCase):
    def test_refuses_sed_in_place(self) -> None:
        for command in (
            "sed -i '310s/a/b/' PLAN.md",
            "sed -i.bak 's/a/b/' laserforce_simulator/matches/views.py",
            "sed --in-place 's/a/b/' README.md",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.refusal(command))

    def test_refuses_a_heredoc_into_a_repo_file(self) -> None:
        for command in (
            "cat >> laserforce_simulator/matches/tests/test_x.py <<'EOF'\nx = 1\nEOF",
            "cat <<EOF > frontend/src/app/a.ts\nx\nEOF",
            "tee docs/agents/x.md <<'EOF'\nx\nEOF",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.refusal(command))

    def test_refuses_a_redirect_into_a_source_file(self) -> None:
        self.assertIsNotNone(guard.refusal("echo 'x = 1' > laserforce_simulator/a.py"))


class TestAllowsEverydayCommands(TestCase):
    def test_allows_reads_and_stream_edits(self) -> None:
        for command in (
            "sed -n '1,40p' PLAN.md",
            "git diff | sed 's/a/b/'",
            "grep -n foo README.md 2>/dev/null",
            "npm --prefix frontend run test:ci 2>&1 | tail -5",
            "python -m pytest laserforce_simulator -q > /dev/null",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.refusal(command))

    def test_allows_writes_under_the_scratchpad_or_temp(self) -> None:
        for command in (
            f"cat > {SCRATCH}/msg.txt <<'EOF'\nhi\nEOF",
            f"python script.py > {SCRATCH}/out.json",
            "sed -i 's/a/b/' /tmp/notes.txt",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.refusal(command))


class TestHookProtocol(TestCase):
    def test_exit_code_2_with_a_message_blocks_the_call(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "sed -i 's/a/b/' x.md"},
        }
        code, message = guard.decide(payload)
        self.assertEqual(code, 2)
        self.assertIn("Edit", message)

    def test_other_tools_pass(self) -> None:
        self.assertEqual(guard.decide({"tool_name": "Read", "tool_input": {}}), (0, ""))
