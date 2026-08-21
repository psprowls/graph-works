"""README's exit-code table stays in sync with exit_codes.py's declared constants."""

from __future__ import annotations

from pathlib import Path

from code_graph_io import exit_codes

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _declared_constant_names() -> list[str]:
    return [name for name in vars(exit_codes) if name.isupper() and not name.startswith("_")]


def test_every_declared_exit_code_appears_in_readme() -> None:
    readme = (_PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    missing = [name for name in _declared_constant_names() if name not in readme]
    assert not missing, f"exit_codes.py declares constants not documented in README.md: {missing}"
