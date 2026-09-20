"""`run_code_excerpt`: a context window around cited lines, or a refusal in the result."""

from __future__ import annotations

from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.code_read import language_for, run_code_excerpt

TODAY = date(2026, 9, 19)
TEN = "".join(f"l{n}\n" for n in range(1, 11))


@pytest.fixture
def layout(tmp_path, git_repo, declare_repos):
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(host / ".works", today=TODAY, topic="Code")).layout
    code = git_repo(
        tmp_path / "code",
        {
            "ten.py": TEN,
            "big.py": "".join(f"b{n}\n" for n in range(1, 1001)),
            "nonl.py": "one\ntwo",
            "crlf.py": "a\r\nb\r\n",
            "ff.py": "a\x0cb\nc\n",
            "bad.py": b"ok\n\xff\xfe\n",
            "empty.py": "",
            "doc.md": "# d\n",
            "data.xyz": "x\n",
            "skip.py": "s\n",
        },
    )
    declare_repos(layout, {"code": (code, ["skip.py"])})
    return layout


def test_the_window_is_five_lines_of_context_clamped_at_both_ends(layout):
    head = run_code_excerpt(layout, "code", "ten.py", 2, 3)
    assert (head.first, head.last, head.total_lines, head.refusal) == (1, 8, 10, None)
    assert head.lines == tuple(f"l{n}" for n in range(1, 9))
    tail = run_code_excerpt(layout, "code", "ten.py", 9)
    assert (tail.start, tail.end, tail.first, tail.last) == (9, 9, 4, 10)


def test_end_defaults_to_start(layout):
    result = run_code_excerpt(layout, "code", "ten.py", 6)
    assert (result.end, result.first, result.last) == (6, 1, 10)


def test_the_span_is_capped_and_the_effective_end_reported(layout):
    result = run_code_excerpt(layout, "code", "big.py", 10, 900)
    assert result.end == 410
    assert (result.first, result.last) == (5, 415)
    assert len(result.lines) == 411


def test_a_file_without_a_trailing_newline(layout):
    result = run_code_excerpt(layout, "code", "nonl.py", 2)
    assert (result.total_lines, result.lines) == (2, ("one", "two"))


def test_line_endings_are_normalized_and_only_newlines_split(layout):
    assert run_code_excerpt(layout, "code", "crlf.py", 1).lines == ("a", "b")
    assert run_code_excerpt(layout, "code", "ff.py", 1).lines == ("a\x0cb", "c")


def test_invalid_bytes_decode_with_replacement(layout):
    assert run_code_excerpt(layout, "code", "bad.py", 2).lines == ("ok", "\ufffd\ufffd")


def test_start_past_eof_is_out_of_range(layout):
    for path, total in (("ten.py", 10), ("empty.py", 0)):
        result = run_code_excerpt(layout, "code", path, 11)
        assert (result.refusal, result.total_lines, result.lines) == ("out-of-range", total, ())
        assert (result.first, result.last) == (None, None)


def test_language_comes_from_the_extension(layout):
    assert run_code_excerpt(layout, "code", "ten.py", 1).language == "python"
    assert run_code_excerpt(layout, "code", "doc.md", 1).language == "markdown"
    assert run_code_excerpt(layout, "code", "data.xyz", 1).language is None
    assert language_for("a.YML") == "yaml"


@pytest.mark.parametrize(
    ("repo", "path", "refusal"),
    [
        ("nope", "ten.py", "unknown-repository"),
        ("code", "../x.py", "outside-repository"),
        ("code", "skip.py", "unknown-file"),
        ("code", "absent.py", "unknown-file"),
    ],
)
def test_refusals_are_results(layout, repo, path, refusal):
    result = run_code_excerpt(layout, repo, path, 1)
    assert result.refusal == refusal
    assert (result.repo, result.path, result.start, result.end) == (repo, path, 1, 1)
    assert (result.first, result.last, result.total_lines, result.language, result.lines) == (
        None,
        None,
        None,
        None,
        (),
    )


@pytest.mark.parametrize(("start", "end"), [(0, None), (5, 4)])
def test_invalid_bounds_raise_value_error(layout, start, end):
    with pytest.raises(ValueError, match=r"start|end"):
        run_code_excerpt(layout, "code", "ten.py", start, end)
