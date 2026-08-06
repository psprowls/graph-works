"""Acceptance tests for `scripts/convert_wikilinks.py`.

Outside the repo's `testpaths` and coverage `source` list on purpose: `scripts/`
is repo tooling, not a package, so these do not move the 95% gate. Run them
with `uv run pytest scripts/tests`.

Every case here is a named regression drawn from the two live vaults, not an
invented edge case. The code-masking ones in particular: `[[tool.importlinter.contracts]]`
really is in six of this vault's pages, and converting it would corrupt a TOML
sample.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convert_wikilinks import mask_inline_code, run, strip_entity_prefix  # noqa: E402


def page(vault: Path, path: str, body: str, *, frontmatter: str = "") -> Path:
    target = vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return target


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    page(root, "entities/pkg_okf-io.md", "# okf-io\n\nThe core.\n", frontmatter="kind: package\n")
    page(root, "entities/unit_tests_okf-io.md", "# okf-io-unit-tests\n\nTests.\n", frontmatter="kind: test_suite\n")
    page(root, "concepts/okf-bundle-format.md", "# Bundle\n", frontmatter='title: OKF bundle format\n')
    return root


def convert(vault: Path, **kwargs: object) -> str:
    run(vault, write=True, relative=kwargs.pop("relative", False), backfill=kwargs.pop("backfill", True))
    return (vault / kwargs.pop("read")).read_text(encoding="utf-8")


# --- the reason this cannot be a plain regex -------------------------------


def test_fenced_code_is_untouched(vault: Path) -> None:
    """`[[tool.importlinter.contracts]]` is TOML, and appears in six real pages."""
    body = "Prose.\n\n```toml\n[[tool.importlinter.contracts]]\nname = \"x\"\n```\n"
    page(vault, "notes.md", body)
    assert "[[tool.importlinter.contracts]]" in convert(vault, read="notes.md")


def test_inline_code_is_untouched(vault: Path) -> None:
    page(vault, "notes.md", "Backticked `[[entities/pkg_okf-io]]` stays put.\n")
    assert "`[[entities/pkg_okf-io]]`" in convert(vault, read="notes.md")


def test_double_backtick_span_is_untouched(vault: Path) -> None:
    """`okf_io._md`'s simpler `[^`]*` pattern mis-pairs this one."""
    page(vault, "notes.md", "A ``[[entities/pkg_okf-io]]`` span.\n")
    assert "``[[entities/pkg_okf-io]]``" in convert(vault, read="notes.md")


def test_python_type_syntax_is_untouched(vault: Path) -> None:
    """`Callable[[EntryTarget], str | None]` is real prose in this vault."""
    page(vault, "notes.md", "See `Callable[[EntryTarget], str | None]` in index.py.\n")
    assert "Callable[[EntryTarget], str | None]" in convert(vault, read="notes.md")


def test_unbalanced_across_lines_does_not_join(vault: Path) -> None:
    page(vault, "notes.md", "An unclosed [[link that never closes.\nAnd a stray]] here.\n")
    out = convert(vault, read="notes.md")
    assert "[[link that never closes." in out


# --- conversion ------------------------------------------------------------


def test_bare_link_uses_the_targets_h1(vault: Path) -> None:
    page(vault, "notes.md", "See [[entities/pkg_okf-io]].\n")
    assert "[okf-io](/entities/pkg_okf-io.md)" in convert(vault, read="notes.md")


def test_bare_link_prefers_frontmatter_title_over_h1(vault: Path) -> None:
    page(vault, "notes.md", "See [[concepts/okf-bundle-format]].\n")
    assert "[OKF bundle format](/concepts/okf-bundle-format.md)" in convert(vault, read="notes.md")


def test_stem_fallback_would_collide_so_h1_wins(vault: Path) -> None:
    """`unit_tests_okf-io` strips to `okf-io` -- already `pkg_okf-io`'s name."""
    page(vault, "notes.md", "See [[entities/unit_tests_okf-io]].\n")
    assert "[okf-io-unit-tests](/entities/unit_tests_okf-io.md)" in convert(vault, read="notes.md")


def test_alias_wins_over_everything(vault: Path) -> None:
    page(vault, "notes.md", "See [[entities/pkg_okf-io|the core]].\n")
    assert "[the core](/entities/pkg_okf-io.md)" in convert(vault, read="notes.md")


def test_table_cell_escaped_pipe_alias(vault: Path) -> None:
    page(vault, "notes.md", "| a | b |\n|---|---|\n| [[entities/pkg_okf-io\\|core]] | x |\n")
    assert "[core](/entities/pkg_okf-io.md)" in convert(vault, read="notes.md")


def test_anchor_is_preserved(vault: Path) -> None:
    page(vault, "notes.md", "See [[entities/pkg_okf-io#Design]].\n")
    assert "[okf-io](/entities/pkg_okf-io.md#Design)" in convert(vault, read="notes.md")


def test_relative_flag_emits_file_relative_destinations(vault: Path) -> None:
    page(vault, "concepts/notes.md", "See [[entities/pkg_okf-io]].\n")
    out = convert(vault, read="concepts/notes.md", relative=True)
    assert "[okf-io](../entities/pkg_okf-io.md)" in out


def test_multiple_links_on_one_line(vault: Path) -> None:
    page(vault, "notes.md", "Both [[entities/pkg_okf-io]] and [[concepts/okf-bundle-format]] here.\n")
    out = convert(vault, read="notes.md")
    assert "[okf-io](/entities/pkg_okf-io.md)" in out
    assert "[OKF bundle format](/concepts/okf-bundle-format.md)" in out


# --- what must not be rewritten -------------------------------------------


def test_unresolved_target_is_left_byte_identical(vault: Path) -> None:
    """`.templates/` placeholders and prose about wikilinks both land here."""
    body = "A placeholder [[entities/pkg_<pkg>]] and prose about [[wikilink]].\n"
    page(vault, "notes.md", body)
    out = convert(vault, read="notes.md")
    assert "[[entities/pkg_<pkg>]]" in out
    assert "[[wikilink]]" in out


def test_empty_target_is_not_matched(vault: Path) -> None:
    page(vault, "notes.md", "Empty [[]] and blank [[ ]].\n")
    out = convert(vault, read="notes.md")
    assert "[[]]" in out
    assert "[[ ]]" in out


def test_embed_is_left_alone(vault: Path) -> None:
    page(vault, "notes.md", "An embed ![[entities/pkg_okf-io]] renders differently.\n")
    assert "![[entities/pkg_okf-io]]" in convert(vault, read="notes.md")


def test_ambiguous_bare_name_resolves_to_neither(vault: Path) -> None:
    page(vault, "a/dupe.md", "# A\n")
    page(vault, "b/dupe.md", "# B\n")
    page(vault, "notes.md", "See [[dupe]].\n")
    assert "[[dupe]]" in convert(vault, read="notes.md")


# --- regressions -----------------------------------------------------------


def test_alias_containing_inline_code_keeps_its_text(vault: Path) -> None:
    """The masked copy is for *matching* only; groups must be read back from
    the original line. Reading `match.group()` wrote NUL bytes into the label
    for every aliased link whose text contained backticks -- 17 of them in this
    vault's `index.md` alone."""
    page(vault, "notes.md", "See [[entities/pkg_okf-io|the `okf-io` core]].\n")
    out = convert(vault, read="notes.md")
    assert "\x00" not in out
    assert "[the `okf-io` core](/entities/pkg_okf-io.md)" in out


def test_no_nul_bytes_anywhere(vault: Path) -> None:
    page(vault, "notes.md", "A `code` span, [[entities/pkg_okf-io|a `b` c]], and ``dbl``.\n")
    assert "\x00" not in convert(vault, read="notes.md")


def test_anchor_containing_inline_code_is_unmasked(vault: Path) -> None:
    page(vault, "notes.md", "See [[entities/pkg_okf-io#the `x` bit]].\n")
    out = convert(vault, read="notes.md")
    assert "\x00" not in out
    assert "#the `x` bit)" in out


def test_reserved_index_does_not_gain_frontmatter(vault: Path) -> None:
    """§12 permits only `okf_version` on `index.md`; this vault's has no
    frontmatter at all, so a backfilled `title:` would staple a whole block on
    and trip `reserved.index-frontmatter`."""
    (vault / "index.md").write_text("# Index\n\nCatalog.\n", encoding="utf-8")
    page(vault, "notes.md", "See [[index]].\n")
    convert(vault, read="notes.md")
    text = (vault / "index.md").read_text(encoding="utf-8")
    assert text.startswith("# Index")
    assert "title:" not in text


def test_reserved_log_does_not_gain_frontmatter(vault: Path) -> None:
    (vault / "log.md").write_text("# Log\n\n## [2026-08-06] note | x\n", encoding="utf-8")
    page(vault, "notes.md", "See [[log]].\n")
    convert(vault, read="notes.md")
    assert not (vault / "log.md").read_text(encoding="utf-8").startswith("---")


def test_link_into_reserved_file_still_converts(vault: Path) -> None:
    """Excluding reserved files from *backfill* must not exclude them as targets."""
    (vault / "index.md").write_text("# Index\n\nCatalog.\n", encoding="utf-8")
    page(vault, "notes.md", "See [[index]].\n")
    assert "[Index](/index.md)" in convert(vault, read="notes.md")


# --- title backfill --------------------------------------------------------


def test_title_backfilled_on_a_targeted_page(vault: Path) -> None:
    page(vault, "notes.md", "See [[entities/pkg_okf-io]].\n")
    convert(vault, read="notes.md")
    assert "title: okf-io" in (vault / "entities/pkg_okf-io.md").read_text(encoding="utf-8")


def test_untargeted_page_is_not_backfilled(vault: Path) -> None:
    page(vault, "notes.md", "Nothing links to the test suite.\n")
    convert(vault, read="notes.md")
    assert "title:" not in (vault / "entities/unit_tests_okf-io.md").read_text(encoding="utf-8")


def test_backfill_can_be_disabled(vault: Path) -> None:
    page(vault, "notes.md", "See [[entities/pkg_okf-io]].\n")
    convert(vault, read="notes.md", backfill=False)
    assert "title:" not in (vault / "entities/pkg_okf-io.md").read_text(encoding="utf-8")


def test_existing_title_is_not_overwritten(vault: Path) -> None:
    page(vault, "notes.md", "See [[concepts/okf-bundle-format]].\n")
    convert(vault, read="notes.md")
    text = (vault / "concepts/okf-bundle-format.md").read_text(encoding="utf-8")
    assert "title: OKF bundle format" in text


# --- writer conventions ----------------------------------------------------


def test_dry_run_writes_nothing(vault: Path) -> None:
    target = page(vault, "notes.md", "See [[entities/pkg_okf-io]].\n")
    before = target.read_bytes()
    result = run(vault, write=False, relative=False, backfill=True)
    assert result.conversions
    assert target.read_bytes() == before


def test_conversion_is_idempotent(vault: Path) -> None:
    target = page(vault, "notes.md", "See [[entities/pkg_okf-io]] and [[wikilink]].\n")
    run(vault, write=True, relative=False, backfill=True)
    once = target.read_bytes()
    run(vault, write=True, relative=False, backfill=True)
    assert target.read_bytes() == once


def test_untouched_file_keeps_its_exact_bytes(vault: Path) -> None:
    target = page(vault, "notes.md", "No links here at all.\n")
    before = target.read_bytes()
    run(vault, write=True, relative=False, backfill=True)
    assert target.read_bytes() == before


def test_only_the_link_changes_on_a_converted_line(vault: Path) -> None:
    page(vault, "notes.md", "Leading text [[entities/pkg_okf-io]] trailing text.\n")
    out = convert(vault, read="notes.md")
    assert "Leading text [okf-io](/entities/pkg_okf-io.md) trailing text." in out


# --- units -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("pkg_okf-io", "okf-io"),
        ("dep_ruamel.yaml", "ruamel.yaml"),
        ("unit_tests_okf-io", "okf-io"),
        ("repo_agent-workspace", "agent-workspace"),
        ("no-prefix-here", "no-prefix-here"),
        ("pkg_", "pkg_"),
    ],
)
def test_strip_entity_prefix(stem: str, expected: str) -> None:
    assert strip_entity_prefix(stem) == expected


def test_mask_inline_code_preserves_offsets() -> None:
    line = "a `code` b"
    assert len(mask_inline_code(line)) == len(line)
    assert mask_inline_code(line).startswith("a ")
    assert mask_inline_code(line).endswith(" b")


def test_mask_inline_code_leaves_unpaired_backtick_alone() -> None:
    assert mask_inline_code("a ` b") == "a ` b"
