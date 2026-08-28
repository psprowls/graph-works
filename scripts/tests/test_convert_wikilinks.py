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

from convert_wikilinks import mask_inline_code, report, run, strip_entity_prefix


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
    page(root, "concepts/okf-bundle-format.md", "# Bundle\n", frontmatter="title: OKF bundle format\n")
    return root


def convert(vault: Path, **kwargs: object) -> str:
    run(vault, write=True, relative=kwargs.pop("relative", False), backfill=kwargs.pop("backfill", True))
    return (vault / kwargs.pop("read")).read_text(encoding="utf-8")


# --- the reason this cannot be a plain regex -------------------------------


def test_fenced_code_is_untouched(vault: Path) -> None:
    """`[[tool.importlinter.contracts]]` is TOML, and appears in six real pages."""
    body = 'Prose.\n\n```toml\n[[tool.importlinter.contracts]]\nname = "x"\n```\n'
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


# --- ladder step 2: legacy work-item slug repair ---------------------------


def item_vault(vault: Path) -> Path:
    """The work-lane shapes this sweep has to repair, drawn from the live bundle."""
    page(
        vault,
        "work/_archive/epic-graph-works-cli.md",
        "# CLI\n",
        frontmatter="title: graph-works-cli — port the gw binary\n",
    )
    page(
        vault,
        "work/_archive/bug-scan-skips-mirror-lane.md",
        "# Mirror\n",
        frontmatter="title: scan skips the mirror lane\n",
    )
    page(
        vault,
        "work/_archive/epic-graph-works-plugin-fork/children/_archive/spike-epic-spike-obra-rebase.md",
        "# Obra\n",
        frontmatter="title: obra rebase spike\n",
    )
    return vault


def test_work_prefixed_dated_slug_resolves_to_the_current_item(vault: Path) -> None:
    item_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli]].\n")
    out = convert(vault, read="notes.md", backfill=False)
    assert "[graph-works-cli — port the gw binary](/work/_archive/epic-graph-works-cli.md)" in out


def test_archive_prefixed_dated_slug_resolves(vault: Path) -> None:
    item_vault(vault)
    page(vault, "notes.md", "See [[work/_archive/2026-08-11-epic-graph-works-cli]].\n")
    assert "(/work/_archive/epic-graph-works-cli.md)" in convert(vault, read="notes.md", backfill=False)


def test_bare_dated_slug_without_work_prefix_resolves(vault: Path) -> None:
    """112 live occurrences carry no `work/` prefix; the date is what makes them safe."""
    item_vault(vault)
    page(vault, "notes.md", "See [[2026-08-19-bug-scan-skips-mirror-lane]].\n")
    assert "(/work/_archive/bug-scan-skips-mirror-lane.md)" in convert(vault, read="notes.md", backfill=False)


def test_undated_generic_basename_is_not_repaired(vault: Path) -> None:
    """`01-design-spec` alone matches 170+ files in the live bundle. Step 1's
    unique-stem branch already declines an ambiguous name; step 2 must not
    rescue it, because the dated prefix is what licenses a bare slug match.

    Note a *unique* undated basename still resolves -- through step 1, which
    this task does not touch. Only the generic colliding case is at issue.
    """
    item_vault(vault)
    page(vault, "work/a-item/references/01-design-spec.md", "# A\n")
    page(vault, "work/b-item/references/01-design-spec.md", "# B\n")
    page(vault, "notes.md", "See [[01-design-spec]].\n")
    assert "[[01-design-spec]]" in convert(vault, read="notes.md", backfill=False)


def test_historic_epic_kind_alias_is_tried(vault: Path) -> None:
    """The rename produced `spike-epic-spike-obra-rebase`; hyphenated kinds
    (`tech-debt`, `test-gap`) exist too, so the kind list is explicit."""
    item_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-17-epic-spike-obra-rebase]].\n")
    out = convert(vault, read="notes.md", backfill=False)
    assert "spike-epic-spike-obra-rebase.md)" in out


def test_ambiguous_item_slug_is_not_repaired(vault: Path) -> None:
    page(vault, "work/dupe-item.md", "# Live\n")
    page(vault, "work/_archive/dupe-item.md", "# Archived\n")
    page(vault, "notes.md", "See [[work/2026-08-11-dupe-item]].\n")
    assert "[[work/2026-08-11-dupe-item]]" in convert(vault, read="notes.md", backfill=False)


def test_item_slug_naming_nothing_is_left_alone(vault: Path) -> None:
    item_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-tech-debt-never-existed]].\n")
    assert "[[work/2026-08-11-epic-tech-debt-never-existed]]" in convert(vault, read="notes.md", backfill=False)


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


# --- ladder step 3: artifact under item, with demotion ---------------------


def artifact_vault(vault: Path) -> Path:
    item_vault(vault)
    root = "work/_archive/epic-graph-works-plugin-fork/children/_archive/spike-epic-spike-obra-rebase"
    page(
        vault, f"{root}/references/04-reconcile-cost-table.md", "# Costs\n", frontmatter="title: reconcile cost table\n"
    )
    page(
        vault,
        "work/_archive/epic-graph-works-cli/references/_archive/00-decisions.md",
        "# Decisions\n",
        frontmatter="title: decision ledger\n",
    )
    return vault


def test_artifact_under_the_repaired_item_resolves(vault: Path) -> None:
    artifact_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-17-epic-spike-obra-rebase/04-reconcile-cost-table.md]].\n")
    assert (
        "[reconcile cost table](/work/_archive/epic-graph-works-plugin-fork/children/"
        "_archive/spike-epic-spike-obra-rebase/references/04-reconcile-cost-table.md)"
        in convert(vault, read="notes.md", backfill=False)
    )


def test_trailing_md_suffix_is_not_doubled(vault: Path) -> None:
    """Without the `.md` strip the candidate becomes `…/00-decisions.md.md`;
    that single bug accounted for 21 of the live bundle's artifact hits."""
    artifact_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli/00-decisions.md]].\n")
    assert "(/work/_archive/epic-graph-works-cli/references/_archive/00-decisions.md)" in convert(
        vault, read="notes.md", backfill=False
    )


def test_references_archive_variant_is_searched(vault: Path) -> None:
    artifact_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli/references/00-decisions]].\n")
    assert "(/work/_archive/epic-graph-works-cli/references/_archive/00-decisions.md)" in convert(
        vault, read="notes.md", backfill=False
    )


def test_unresolvable_artifact_demotes_to_the_item_page(vault: Path) -> None:
    """The item page is the durable identity and its `references/index.md`
    reaches the artifact; a dangling artifact link would reach nothing."""
    artifact_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli/references/99-gone]].\n")
    out = convert(vault, read="notes.md", backfill=False)
    assert "[graph-works-cli — port the gw binary](/work/_archive/epic-graph-works-cli.md)" in out


def test_demotion_is_reported_as_its_own_step(vault: Path) -> None:
    artifact_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli/references/99-gone]].\n")
    result = run(vault, write=False, relative=False, backfill=False)
    assert [c.step for c in result.conversions] == ["demoted"]


def test_artifact_under_an_unresolvable_item_is_left_alone(vault: Path) -> None:
    artifact_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-tech-debt-never-existed/01-design-spec]].\n")
    assert "[[work/2026-08-11-epic-tech-debt-never-existed/01-design-spec]]" in convert(
        vault, read="notes.md", backfill=False
    )


# --- anchors, across the ladder ---------------------------------------------


def test_anchor_is_preserved_through_step_2_item_resolution(vault: Path) -> None:
    item_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli#8-1-mode-skeletons]].\n")
    out = convert(vault, read="notes.md", backfill=False)
    assert "(/work/_archive/epic-graph-works-cli.md#8-1-mode-skeletons)" in out


def test_anchor_is_preserved_through_step_3_artifact_resolution(vault: Path) -> None:
    artifact_vault(vault)
    page(
        vault,
        "notes.md",
        "See [[work/2026-08-17-epic-spike-obra-rebase/04-reconcile-cost-table#8-1-mode-skeletons]].\n",
    )
    out = convert(vault, read="notes.md", backfill=False)
    assert (
        "(/work/_archive/epic-graph-works-plugin-fork/children/"
        "_archive/spike-epic-spike-obra-rebase/references/04-reconcile-cost-table.md#8-1-mode-skeletons)" in out
    )


def test_anchor_is_dropped_on_demotion(vault: Path) -> None:
    """Demotion swaps the destination to the item page; an anchor written
    against the original artifact almost never exists there, so it must not
    be carried across."""
    artifact_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli/references/99-gone#8-1-mode-skeletons]].\n")
    out = convert(vault, read="notes.md", backfill=False)
    assert "[graph-works-cli — port the gw binary](/work/_archive/epic-graph-works-cli.md)" in out
    assert "#8-1-mode-skeletons" not in out


# --- ladder step 4: de-link ------------------------------------------------


def delinked(vault: Path, read: str) -> str:
    run(vault, write=True, relative=False, backfill=False, delink_unresolved=True)
    return (vault / read).read_text(encoding="utf-8")


def test_unresolved_target_is_delinked_when_asked(vault: Path) -> None:
    page(vault, "notes.md", "Prose about [[wikilink]] syntax.\n")
    assert "`[[wikilink]]`" in delinked(vault, "notes.md")


def test_delink_preserves_the_historical_name_verbatim(vault: Path) -> None:
    page(vault, "notes.md", "See [[concepts/graph-works-tier-architecture]].\n")
    assert "`[[concepts/graph-works-tier-architecture]]`" in delinked(vault, "notes.md")


def test_delink_preserves_an_alias(vault: Path) -> None:
    page(vault, "notes.md", "See [[nothing/here|some text]].\n")
    assert "`[[nothing/here|some text]]`" in delinked(vault, "notes.md")


def test_delink_fences_around_inner_backticks(vault: Path) -> None:
    """A CommonMark span needs a fence longer than any run it contains, and
    padding when the content starts or ends with a backtick."""
    page(vault, "notes.md", "See [[nothing|a `b` c]].\n")
    out = delinked(vault, "notes.md")
    assert "``[[nothing|a `b` c]]``" in out


def test_delink_is_off_by_default(vault: Path) -> None:
    page(vault, "notes.md", "Prose about [[wikilink]] syntax.\n")
    assert "[[wikilink]]" in convert(vault, read="notes.md", backfill=False)
    assert "`[[wikilink]]`" not in convert(vault, read="notes.md", backfill=False)


def test_delink_is_idempotent(vault: Path) -> None:
    target = page(vault, "notes.md", "Prose about [[wikilink]] syntax.\n")
    run(vault, write=True, relative=False, backfill=False, delink_unresolved=True)
    once = target.read_bytes()
    run(vault, write=True, relative=False, backfill=False, delink_unresolved=True)
    assert target.read_bytes() == once


def test_delink_leaves_embeds_alone(vault: Path) -> None:
    page(vault, "notes.md", "An embed ![[nothing/here]] renders differently.\n")
    assert "![[nothing/here]]" in delinked(vault, "notes.md")


def test_delink_is_reported_as_its_own_reason(vault: Path) -> None:
    page(vault, "notes.md", "Prose about [[wikilink]] syntax.\n")
    result = run(vault, write=False, relative=False, backfill=False, delink_unresolved=True)
    assert [s.reason for s in result.skips] == ["delinked"]


def test_delink_declines_when_a_trailing_backtick_is_adjacent(vault: Path) -> None:
    """A stray unpaired backtick right after the match would merge with the
    inserted fence into a wider, unpairable run -- leave it alone instead."""
    source = "See [[nothing]]`s example.\n"
    page(vault, "notes.md", source)
    out = delinked(vault, "notes.md")
    assert out.endswith(source)
    assert "[[nothing]]" in out
    assert "`[[nothing]]`" not in out


def test_delink_declines_when_a_leading_backtick_is_adjacent(vault: Path) -> None:
    source = "code`[[nothing]] rest\n"
    page(vault, "notes.md", source)
    out = delinked(vault, "notes.md")
    assert out.endswith(source)
    assert "[[nothing]]" in out
    assert "`[[nothing]]`" not in out


def test_delink_declines_when_a_closed_single_backtick_span_is_flush(vault: Path) -> None:
    """A well-formed `code`[[nothing]] span has its backticks masked out of
    `scannable` by `mask_inline_code`, so the guard must consult the raw line
    -- otherwise the inserted fence merges with the span into one wide,
    unpairable run and the wikilink stops rendering as a code span at all."""
    source = "Trailing span: `code`[[nothing]] rest.\n"
    page(vault, "notes.md", source)
    out = delinked(vault, "notes.md")
    assert out.endswith(source)
    assert "`code`" in out
    assert "[[nothing]]" in out


def test_delink_declines_when_a_closed_double_backtick_span_is_flush(vault: Path) -> None:
    """Same hazard, but for a double-backtick span -- this is the destructive
    case: without consulting the raw line, the merged run has no pairs left
    and the whole line renders as literal text instead of a code span."""
    source = "Double: ``x``[[nothing]] rest.\n"
    page(vault, "notes.md", source)
    out = delinked(vault, "notes.md")
    assert out.endswith(source)
    assert "``x``" in out
    assert "[[nothing]]" in out


# --- the target map --------------------------------------------------------


def test_mapping_records_each_distinct_target_once(vault: Path) -> None:
    item_vault(vault)
    page(
        vault,
        "notes.md",
        "Twice: [[work/2026-08-11-epic-graph-works-cli]] and [[work/2026-08-11-epic-graph-works-cli]].\n",
    )
    result = run(vault, write=False, relative=False, backfill=False)
    assert result.mapping["work/2026-08-11-epic-graph-works-cli"] == ("work/_archive/epic-graph-works-cli.md", "item")


def test_mapping_records_the_step_that_resolved_it(vault: Path) -> None:
    artifact_vault(vault)
    page(
        vault,
        "notes.md",
        "A [[entities/pkg_okf-io]] and a [[work/2026-08-11-epic-graph-works-cli/references/99-gone]].\n",
    )
    result = run(vault, write=False, relative=False, backfill=False)
    assert result.mapping["entities/pkg_okf-io"][1] == "exact"
    assert result.mapping["work/2026-08-11-epic-graph-works-cli/references/99-gone"][1] == "demoted"


def test_report_prints_the_map_on_a_dry_run(vault: Path, capsys) -> None:
    item_vault(vault)
    page(vault, "notes.md", "See [[work/2026-08-11-epic-graph-works-cli]].\n")
    report(run(vault, write=False, relative=False, backfill=False), write=False)
    out = capsys.readouterr().out
    assert "target map" in out
    assert "work/2026-08-11-epic-graph-works-cli -> work/_archive/epic-graph-works-cli.md" in out


def test_report_map_groups_by_step(vault: Path, capsys) -> None:
    artifact_vault(vault)
    page(vault, "notes.md", "A [[entities/pkg_okf-io]] and a [[work/2026-08-11-epic-graph-works-cli]].\n")
    report(run(vault, write=False, relative=False, backfill=False), write=False)
    out = capsys.readouterr().out
    assert "[exact]" in out
    assert "[item]" in out


def test_report_prints_delinked_targets(vault: Path, capsys) -> None:
    page(vault, "notes.md", "Prose about [[wikilink]] syntax.\n")
    result = run(vault, write=False, relative=False, backfill=False, delink_unresolved=True)
    report(result, write=False)
    out = capsys.readouterr().out
    assert "de-linked" in out
    assert "wikilink" in out
