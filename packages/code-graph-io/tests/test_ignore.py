"""Tests for the directory-skip floor and the config-driven ignore matcher."""

from __future__ import annotations

from code_graph_io import _ignore


def test_default_skip_dirs_contents() -> None:
    assert (
        frozenset(
            {
                ".git",
                "node_modules",
                ".worktrees",
                ".venv",
                "venv",
                "dist",
                "build",
                "__pycache__",
                ".tox",
                ".nox",
            }
        )
        == _ignore.DEFAULT_SKIP_DIRS
    )


def test_should_skip_matches_any_path_component() -> None:
    skip = frozenset({"dist", "node_modules"})
    assert _ignore.should_skip("dist/foo.py", skip)
    assert _ignore.should_skip("packages/x/node_modules/y/z.js", skip)
    assert _ignore.should_skip("dist", skip)
    assert not _ignore.should_skip("packages/x/src/a.py", skip)
    assert not _ignore.should_skip("distance.py", skip)  # substring is NOT a match


def test_should_skip_ignore_defaults_to_none_and_changes_nothing() -> None:
    skip = frozenset({"dist"})
    assert not _ignore.should_skip("packages/x/src/a.py", skip)
    assert not _ignore.should_skip("packages/x/src/a.py", skip, None)


def test_compile_ignore_of_no_patterns_matches_nothing() -> None:
    spec = _ignore.compile_ignore(())
    assert not spec.matches("anything.py")
    assert not spec.matches("")


def test_star_and_question_do_not_cross_a_slash() -> None:
    spec = _ignore.compile_ignore(("src/*/tests",))
    assert spec.matches("src/pkg_a/tests")
    assert not spec.matches("src/pkg_a/nested/tests")  # fnmatch would wrongly match this


def test_question_mark_matches_exactly_one_non_slash_char() -> None:
    spec = _ignore.compile_ignore(("fixture?.py",))
    assert spec.matches("fixture1.py")
    assert not spec.matches("fixture12.py")
    assert not spec.matches("fixture/.py")


def test_double_star_crosses_slash_boundaries() -> None:
    spec = _ignore.compile_ignore(("**/fixtures/**",))
    assert spec.matches("packages/code-graph-io/tests/fixtures/sample_monorepo/pkg_a/pyproject.toml")
    assert spec.matches("fixtures/x")
    assert not spec.matches("packages/fixtures_extra/x.py")  # not a `/fixtures/` component


def test_bare_name_pattern_matches_only_that_exact_full_path() -> None:
    """The `.graphignore` discrepancy this item fixes: a bare directory name
    used to skip every nested occurrence of that name. Under the new
    matcher it means exactly what it says — the full relative path must
    equal it — so a real config author writes `**/fixtures/**` instead."""
    spec = _ignore.compile_ignore(("fixtures",))
    assert spec.matches("fixtures")
    assert not spec.matches("packages/code-graph-io/tests/fixtures")
    assert not spec.matches("packages/code-graph-io/tests/fixtures/sample_monorepo/pkg_a/pyproject.toml")


def test_bracket_expression_matches_a_character_set() -> None:
    spec = _ignore.compile_ignore(("fixture[12].py",))
    assert spec.matches("fixture1.py")
    assert spec.matches("fixture2.py")
    assert not spec.matches("fixture3.py")


def test_negated_bracket_expression() -> None:
    spec = _ignore.compile_ignore(("fixture[!12].py",))
    assert spec.matches("fixture3.py")
    assert not spec.matches("fixture1.py")


def test_unterminated_bracket_is_treated_as_a_literal_char() -> None:
    spec = _ignore.compile_ignore(("weird[file.py",))
    assert spec.matches("weird[file.py")
    assert not spec.matches("weirdXfile.py")


def test_multiple_patterns_are_or_ed() -> None:
    spec = _ignore.compile_ignore(("**/dist/**", "**/*.generated.py"))
    assert spec.matches("packages/x/dist/bundle.js")
    assert spec.matches("packages/x/foo.generated.py")
    assert not spec.matches("packages/x/src/a.py")


def test_graphignore_machinery_is_gone() -> None:
    assert not hasattr(_ignore, "GRAPHIGNORE_FILENAME")
    assert not hasattr(_ignore, "_read_graphignore")
    assert not hasattr(_ignore, "load_skip_dirs")
