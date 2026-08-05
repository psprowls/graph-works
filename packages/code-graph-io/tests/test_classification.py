"""Unit tests for code_graph_io.classification — pure classify() function.

Covers signal extraction, framework precedence, and
the package/app discriminator. classify() must remain pure — no SQLite,
no subprocess, no logging side effects. The 8 tests below pin every
documented behavior.
"""

from __future__ import annotations

from pathlib import Path

from code_graph_io.classification import classify


def test_classify_no_signals_stays_package(tmp_path: Path) -> None:
    """zero signals → ('package', None, [])."""
    info = {"language": "python", "scripts_present": False}
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "package"
    assert app_kind is None
    assert signals == []


def test_classify_python_scripts_cli(tmp_path: Path) -> None:
    """Python scripts_present=True → ('app', 'cli', ['cli'])."""
    info = {"language": "python", "scripts_present": True}
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "cli"
    assert signals == ["cli"]


def test_classify_js_bin_cli(tmp_path: Path) -> None:
    """JS bin_present=True with no framework deps → ('app', 'cli', ['cli'])."""
    info = {
        "language": "javascript",
        "bin_present": True,
        "dependencies": ["lodash"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "cli"
    assert signals == ["cli"]


def test_classify_js_next(tmp_path: Path) -> None:
    """JS dependencies containing 'next' → ('app', 'nextjs', [...])."""
    info = {
        "language": "javascript",
        "bin_present": False,
        "dependencies": ["next", "react"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "nextjs"
    assert signals == ["nextjs"]


def test_classify_js_expo(tmp_path: Path) -> None:
    """JS dependencies containing 'expo' → ('app', 'expo', [...])."""
    info = {
        "language": "javascript",
        "bin_present": False,
        "dependencies": ["expo", "react-native"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "expo"
    assert signals == ["expo"]


def test_classify_js_vite_spa_with_index_html(tmp_path: Path) -> None:
    """vite dep AND index.html present → spa signal."""
    (tmp_path / "index.html").write_text("<!doctype html><html></html>")
    info = {
        "language": "javascript",
        "bin_present": False,
        "dependencies": ["vite", "react"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "spa"
    assert signals == ["spa"]


def test_classify_js_vite_without_index_html_no_spa(tmp_path: Path) -> None:
    """vite dep WITHOUT index.html → no spa signal → stays package."""
    info = {
        "language": "javascript",
        "bin_present": False,
        "dependencies": ["vite", "react"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "package"
    assert app_kind is None
    assert signals == []


def test_classify_multi_signal_precedence_nextjs_over_cli(tmp_path: Path) -> None:
    """nextjs + cli signals → app_kind='nextjs'; signals sorted alphabetically."""
    info = {
        "language": "javascript",
        "bin_present": True,
        "dependencies": ["next", "react"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "nextjs"
    assert signals == ["cli", "nextjs"]


def test_classify_js_electron(tmp_path: Path) -> None:
    """'electron' in deps → ('app', 'electron', ['electron'])."""
    info = {
        "language": "javascript",
        "bin_present": False,
        "dependencies": ["electron"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "electron"
    assert signals == ["electron"]


def test_classify_js_electron_before_spa(tmp_path: Path) -> None:
    """electron + vite + index.html → app_kind='electron', not 'spa' (precedence)."""
    (tmp_path / "index.html").write_text("<!doctype html><html></html>")
    info = {
        "language": "javascript",
        "bin_present": False,
        "dependencies": ["electron", "vite"],
    }
    kind, app_kind, signals = classify(info, tmp_path)
    assert kind == "app"
    assert app_kind == "electron"
    assert "electron" in signals
    assert "spa" in signals  # spa signal fires, but electron wins precedence
