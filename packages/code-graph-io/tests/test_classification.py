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


# ---------- Python server signal ----------


def _py(deps: list[str], *, scripts: bool = False) -> dict[str, object]:
    return {"language": "python", "scripts_present": scripts, "dependencies": deps}


def test_fastapi_dep_plus_app_instantiation_is_server(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_bytes(b"from fastapi import FastAPI\n\napp = FastAPI()\n")

    assert classify(_py(["fastapi>=0.110"]), tmp_path) == ("app", "server", ["server"])


def test_server_dep_without_entry_evidence_stays_package(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_bytes(b"from fastapi import APIRouter\n\nrouter = APIRouter()\n")

    assert classify(_py(["fastapi"]), tmp_path) == ("package", None, [])


def test_pyproject_flask_library_stays_package(tmp_path: Path) -> None:
    """A library depending on Flask, with no top-level entry file, is not an App."""
    (tmp_path / "src" / "flask_ext").mkdir(parents=True)
    (tmp_path / "src" / "flask_ext" / "__init__.py").write_bytes(b"from flask import Flask\napp = Flask(__name__)\n")

    assert classify(_py(["Flask>=3"]), tmp_path) == ("package", None, [])


def test_instantiation_without_server_dep_stays_package(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_bytes(b"app = FastAPI()\n")

    assert classify(_py(["requests"]), tmp_path) == ("package", None, [])


def test_uvicorn_run_with_extras_and_pep503_normalisation(tmp_path: Path) -> None:
    (tmp_path / "server.py").write_bytes(b"import uvicorn\nuvicorn.run('x:app')\n")

    assert classify(_py(["Uvicorn[standard]==0.30.1"]), tmp_path)[1] == "server"


def test_django_manage_py_is_server(tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_bytes(b"from django.core.management import execute_from_command_line\n")

    assert classify(_py(["Django>=5"]), tmp_path)[1] == "server"


def test_unreadable_entry_file_gives_no_signal(tmp_path: Path) -> None:
    (tmp_path / "app.py").mkdir()  # opening a directory raises OSError

    assert classify(_py(["fastapi"]), tmp_path) == ("package", None, [])


def test_cli_and_server_signals_server_wins(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_bytes(b"app = Flask(__name__)\n")

    assert classify(_py(["flask"], scripts=True), tmp_path) == ("app", "server", ["cli", "server"])


def test_evidence_beyond_read_limit_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_bytes(b"#" * (64 * 1024) + b"\napp = FastAPI()\n")

    assert classify(_py(["fastapi"]), tmp_path) == ("package", None, [])
