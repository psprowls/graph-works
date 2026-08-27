"""Acceptance tests for `scripts/check_text_io_explicit.py`.

Outside the repo's coverage `source` list on purpose: `scripts/` is repo
tooling, not a package, so these do not move the 95% gate. They are inside
`testpaths` (`scripts/tests`), so `uv run pytest` collects them.

The guard exists because no lint rule can express it: `ruff`'s PLW1514 is
preview-only, covers `encoding=` only, and does not run on `scripts/` or
`plugins/` at all -- and it is the missing `newline=`, not the missing
`encoding=`, that corrupts a CRLF okf document into CR CR LF on Windows.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_text_io_explicit import find_violations, in_scope, main  # noqa: E402


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    return root


def _tracked(root: Path, relative: str, source: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8", newline="\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "fixture", cwd=root)
    return target


def test_shipped_source_is_in_scope_and_tests_are_not():
    assert in_scope("packages/okf-io/src/okf_io/document.py")
    assert in_scope("scripts/migrate_vault.py")
    assert in_scope("scripts/gw-smoke/stub_results.py")
    assert not in_scope("packages/okf-io/tests/test_document.py")
    assert not in_scope("scripts/tests/test_check_text_io_explicit.py")
    assert not in_scope("plugins/graph-works/hooks/x.py")
    assert not in_scope("packages/okf-io/src/okf_io/py.typed")


_CLEAN = '''\
from pathlib import Path


def go(p: Path) -> None:
    p.write_text("x", encoding="utf-8", newline="\\n")
    p.read_text(encoding="utf-8")
    p.write_bytes(b"x")
'''


def test_a_fully_explicit_file_is_clean(tmp_path):
    root = _repo(tmp_path)
    _tracked(root, "scripts/clean.py", _CLEAN)
    assert find_violations(root) == []


def test_an_encoding_less_read_is_reported(tmp_path):
    root = _repo(tmp_path)
    _tracked(root, "scripts/bad.py", "from pathlib import Path\nPath('a').read_text()\n")
    violations = find_violations(root)
    assert [(v.line, v.missing) for v in violations] == [(2, "encoding")]


def test_an_encoding_less_write_is_reported_for_both_keywords(tmp_path):
    root = _repo(tmp_path)
    _tracked(root, "scripts/bad.py", "from pathlib import Path\nPath('a').write_text('x')\n")
    violations = find_violations(root)
    assert [(v.line, v.missing) for v in violations] == [(2, "encoding"), (2, "newline")]


def test_a_newline_less_write_is_reported_even_with_encoding(tmp_path):
    root = _repo(tmp_path)
    _tracked(root, "scripts/bad.py", "from pathlib import Path\nPath('a').write_text('x', encoding='utf-8')\n")
    violations = find_violations(root)
    assert [(v.line, v.missing) for v in violations] == [(2, "newline")]


def test_a_keyword_on_a_continuation_line_is_seen(tmp_path):
    """The case that defeated the design's first grep survey.

    A line-anchored search cannot see a keyword four lines below the call,
    which is why this guard is AST-based. `query/commands.py:256` is the real
    instance.
    """
    root = _repo(tmp_path)
    source = (
        "from pathlib import Path\n"
        "Path('a').write_text(\n"
        "    'x',\n"
        "    encoding='utf-8',\n"
        "    newline='\\n',\n"
        ")\n"
    )
    _tracked(root, "scripts/multiline.py", source)
    assert find_violations(root) == []


def test_binary_modes_and_os_open_are_not_text_io(tmp_path):
    root = _repo(tmp_path)
    source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path('a').open('rb')\n"
        "Path('a').open('wb')\n"
        "open('a', 'wb')\n"
        "os.open('a', os.O_WRONLY)\n"
        "os.fdopen(3, 'wb')\n"
    )
    _tracked(root, "scripts/binary.py", source)
    assert find_violations(root) == []


def test_os_fdopen_in_text_mode_is_reported(tmp_path):
    """`os.fdopen` is a text-mode factory with both defaults.

    The design's own survey missed it; `config_io/projection.py:51`,
    `workflow_local/ledger.py:76` and `workspace/transactions.py:140` are the
    real instances.
    """
    root = _repo(tmp_path)
    _tracked(root, "scripts/fd.py", "import os\nos.fdopen(3, 'w', encoding='utf-8')\n")
    violations = find_violations(root)
    assert [(v.line, v.missing) for v in violations] == [(2, "newline")]


def test_a_module_style_open_takes_its_mode_at_index_one(tmp_path):
    root = _repo(tmp_path)
    _tracked(root, "scripts/modstyle.py", "import io\nio.open('a', 'rb')\n")
    assert find_violations(root) == []


def test_a_computed_mode_is_not_guessed_at(tmp_path):
    root = _repo(tmp_path)
    _tracked(root, "scripts/computed.py", "from pathlib import Path\nm = 'rb'\nPath('a').open(m)\n")
    assert find_violations(root) == []


def test_a_kwargs_splat_is_not_guessed_at(tmp_path):
    root = _repo(tmp_path)
    _tracked(root, "scripts/splat.py", "from pathlib import Path\nopts = {}\nPath('a').write_text('x', **opts)\n")
    assert find_violations(root) == []


def test_a_pragma_on_any_line_the_call_spans_exempts_it(tmp_path):
    root = _repo(tmp_path)
    source = (
        "from pathlib import Path\n"
        "Path('a').write_text(  # text-io-ok: deliberate locale round-trip\n"
        "    'x',\n"
        "    encoding='locale',\n"
        ")\n"
    )
    _tracked(root, "scripts/pragma.py", source)
    assert find_violations(root) == []


def test_out_of_scope_trees_are_not_scanned(tmp_path):
    root = _repo(tmp_path)
    bad = "from pathlib import Path\nPath('a').write_text('x')\n"
    _tracked(root, "scripts/tests/test_x.py", bad)
    _tracked(root, "packages/p/tests/test_y.py", bad)
    _tracked(root, "plugins/z.py", bad)
    assert find_violations(root) == []


def test_main_exits_zero_on_a_clean_tree(tmp_path, capsys):
    root = _repo(tmp_path)
    _tracked(root, "scripts/clean.py", _CLEAN)
    assert main([str(root)]) == 0


def test_main_exits_one_and_names_the_site(tmp_path, capsys):
    root = _repo(tmp_path)
    _tracked(root, "scripts/bad.py", "from pathlib import Path\nPath('a').write_text('x', encoding='utf-8')\n")
    assert main([str(root)]) == 1
    stderr = capsys.readouterr().err
    assert "scripts/bad.py:2" in stderr
    assert "newline=" in stderr


def test_this_repository_has_no_implicit_text_io():
    """The property, asserted against the real tree rather than a fixture.

    `just check`'s `text-io` recipe is the primary gate; this is the same
    assertion inside the suite, so a regression fails `just test` too and does
    not wait for someone to run the full gate.
    """
    root = Path(__file__).resolve().parent.parent.parent
    violations = find_violations(root)
    assert violations == [], "\n" + "\n".join(v.render() for v in violations)
