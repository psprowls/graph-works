"""Run the log skill's exact Python recipe, plus adversarial guard probes."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]


class LogRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skill = (PLUGIN / "skills/log/SKILL.md").read_text(encoding="utf-8")
        match = re.search(r"<<'PY'\n(.*?)\nPY\n", skill, re.DOTALL)
        if match is None:
            raise AssertionError("log skill must provide an executable Python recipe")
        cls.recipe = match.group(1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "log with spaces.md"
        # Split the retired heading literal so the production denylist need not
        # exempt this test file. This is intentionally malformed fixture data.
        bad = "## " + "[2026-04-23] lint | invalid"
        self.before = ("""---
type: Log
---
# Log

## 2026-04-20
- **Lint** early
- **scan** later
- filed unlabelled

## 2026-04-19
- lint | old plain

## 2026-04-21
- **INGEST** latest

## 2026-04-20
- lint | duplicate-day latest
  continuation [link](/somewhere.md)

  Another paragraph.
  - **scan** nested detail

  ```md
""" + "  " + bad + """
  - **lint** fenced bullet
  ```

""" + bad + "\n- **lint** invalid section entry\n").encode()
        self.log.write_bytes(self.before)

    def run_recipe(self, *args):
        result = subprocess.run(
            [sys.executable, "-", str(self.log), *args], input=self.recipe,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(self.log.read_bytes(), self.before, "retrieval must be read-only")
        return result

    def test_chronology_outermost_spans_and_invalid_sections(self):
        result = self.run_recipe()
        self.assertEqual(result.returncode, 0, result.stderr)
        # Rendering nested/code content once, grouping duplicate dates, and
        # retaining unlabelled entries are all observable output contracts.
        self.assertEqual(result.stdout, """## 2026-04-21

- **INGEST** latest

## 2026-04-20

- lint | duplicate-day latest
  continuation [link](/somewhere.md)

  Another paragraph.
  - **scan** nested detail

  ```md
""" + "  ## " + "[2026-04-23] lint | invalid\n" + """  - **lint** fenced bullet
  ```

- filed unlabelled

- **scan** later

- **Lint** early

## 2026-04-19

- lint | old plain

""")
        self.assertEqual(result.stderr.count("Invalid log section"), 1)
        self.assertIn("body line 26", result.stderr)
        self.assertNotIn("invalid section entry", result.stdout)

    def test_filters_precede_limit_and_since_is_inclusive(self):
        result = self.run_recipe("--op", "LiNt", "--since", "2026-04-20", "--last", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("duplicate-day latest", result.stdout)
        self.assertIn("**Lint** early", result.stdout)
        self.assertNotIn("old plain", result.stdout)
        self.assertNotIn("INGEST", result.stdout)
        self.assertNotIn("filed", result.stdout)
        result = self.run_recipe("--op", "scan", "--last", "1")
        self.assertEqual(result.stdout, "## 2026-04-20\n\n- **scan** later\n\n")

    def test_default_limit_counts_entries_not_days(self):
        self.before = ("## 2026-04-20\n" + "".join(f"- item {n}\n" for n in range(12))).encode()
        self.log.write_bytes(self.before)
        result = self.run_recipe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "## 2026-04-20\n\n" + "".join(
            f"- item {n}\n\n" for n in range(11, 1, -1)
        ))

    def test_zero_and_empty_match_are_explained(self):
        for args in (("--last", "0"), ("--op", "delete"), ("--since", "2026-05-01")):
            with self.subTest(args=args):
                result = self.run_recipe(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "No matching log entries.\n")

    def test_bad_arguments_fail_clearly(self):
        for args in (("--last", "-1"), ("--last", "oops"), ("--op", "invented"),
                     ("--since", "20260420"), ("--since", "2026-02-30")):
            with self.subTest(args=args):
                result = self.run_recipe(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)


class LogGuardTests(unittest.TestCase):
    def test_retired_claims_fail_even_in_layout_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "gw"
            shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
            guard = plugin / "tests/test-doc-layout-claims.sh"
            baseline = subprocess.run(["bash", str(guard)], capture_output=True, text=True)
            self.assertEqual(baseline.returncode, 0, baseline.stdout)
            target = plugin / "RELEASE-NOTES.md"
            original = target.read_bytes() if target.exists() else b""
            for claim in ("## " + "[YYYY-MM-DD] <op> | <title>",
                          "## " + "[2026-09-18] lint | check",
                          'grep "^## ' + '\\[" log.md | tail -10'):
                with self.subTest(claim=claim):
                    target.write_bytes(original + b"\n" + claim.encode() + b"\n")
                    result = subprocess.run(["bash", str(guard)], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("RELEASE-NOTES.md carries a retired log", result.stdout)
            target.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
