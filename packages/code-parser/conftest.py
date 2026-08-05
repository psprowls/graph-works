"""conftest.py — add the tests/ directory to sys.path for _fixture_loader imports."""

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent / "tests"
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
