from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path, PurePosixPath

import pytest
from helpers import BUNDLES
from okf_io import bundle, index

CONCEPT = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# T\n"


def make(tmp_path: Path, files: dict[str, str]) -> bundle.Bundle:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return bundle.load(tmp_path)


def plan(loaded: bundle.Bundle, directory: str) -> index._Plan:
    return index._plan(loaded, directory, index._directories(loaded))


def test_reconciling_a_vendored_bundle_finds_nothing_to_do():
    """The go/no-go property (spec §9). A real index is already reconciled.

    If this fails, either the entry-matching rules or the member-resolution
    rules are wrong, and no amount of splice machinery will fix it.
    """
    for name in ("acme_retail", "ga4"):
        loaded = bundle.load(BUNDLES / name)
        for directory in sorted(index._directories(loaded)):
            result = plan(loaded, directory)
            assert result.dead == (), f"{name}:{directory} would prune {result.dead}"
            assert result.missing == (), f"{name}:{directory} would add {result.missing}"


def test_a_root_asset_is_never_a_missing_member():
    """`acme_retail/viz.html` is not in the root index, and must stay that way."""
    loaded = bundle.load(BUNDLES / "acme_retail")
    assert "viz.html" in loaded.assets
    assert plan(loaded, "").missing == ()


def test_a_concept_with_no_entry_is_missing(tmp_path):
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": "# Metric\n\n* [Revenue](revenue.md) - R.\n",
            "metrics/revenue.md": CONCEPT,
            "metrics/margin.md": CONCEPT,
        },
    )
    result = plan(loaded, "metrics")
    assert [target.path for target in result.missing] == ["metrics/margin.md"]
    assert [target.kind for target in result.missing] == ["concept"]
    assert result.dead == ()


def test_a_subdirectory_with_no_entry_is_missing(tmp_path):
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n",
            "metrics/index.md": "# Metric\n",
            "metrics/revenue.md": CONCEPT,
        },
    )
    result = plan(loaded, "")
    assert [(t.path, t.kind) for t in result.missing] == [("metrics", "subdirectory")]


def test_an_entry_pointing_at_a_deleted_file_is_dead(tmp_path):
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": "# Metric\n\n* [Gone](gone.md) - G.\n* [R](revenue.md) - R.\n",
            "metrics/revenue.md": CONCEPT,
        },
    )
    result = plan(loaded, "metrics")
    assert [entry.target for entry in result.dead] == ["metrics/gone.md"]


def test_an_entry_with_an_escaped_hash_reconciles_against_its_member(tmp_path):
    """Regression: decoding before the fragment split reads `report%23v2.md` as
    `report`, so a live entry would be pruned and its member re-added."""
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": "# Metric\n\n* [R](report%23v2.md) - R.\n",
            "metrics/report#v2.md": CONCEPT,
        },
    )
    result = plan(loaded, "metrics")
    assert result.dead == ()
    assert result.missing == ()


def test_a_dead_asset_entry_is_pruned(tmp_path):
    loaded = make(
        tmp_path,
        {"attesters/index.md": "# Attester\n\n* [x](sql_equality.py) - Checks.\n"},
    )
    assert [entry.target for entry in plan(loaded, "attesters").dead] == ["attesters/sql_equality.py"]


def test_a_live_asset_entry_survives_and_is_never_re_added(tmp_path):
    loaded = make(
        tmp_path,
        {
            "attesters/index.md": "# Attester\n\n* [x](sql_equality.py) - Checks.\n",
            "attesters/sql_equality.py": "print()\n",
        },
    )
    result = plan(loaded, "attesters")
    assert result.dead == ()
    assert result.missing == ()


def test_an_entry_naming_a_nested_dot_directory_page_is_not_pruned(tmp_path):
    """Regression: `_alive` falls through to `bundle.has_member`, which answers
    from the walk. While the walk dropped dot-nested members, this entry was
    classified dead and `update_index()` removed it -- deleting the human's
    description text about a page that is really on disk, against ADR-0009
    ("reconcile, don't regenerate") and ADR-0016. `descriptions="preserve"`
    does not protect against this: preserve governs drift, not pruning.
    """
    loaded = make(
        tmp_path,
        {
            "repositories/demo/index.md": (
                "# Demo\n\n* [skill](.agents/skills/x/SKILL.md.md) - hand-written description.\n"
            ),
            "repositories/demo/.agents/skills/x/SKILL.md.md": CONCEPT,
        },
    )
    result = plan(loaded, "repositories/demo")
    assert result.dead == ()
    assert result.missing == ()


def test_a_new_asset_is_never_a_missing_member(tmp_path):
    loaded = make(
        tmp_path,
        {"attesters/index.md": "# Attester\n", "attesters/sql_equality.py": "print()\n"},
    )
    assert plan(loaded, "attesters").missing == ()


def test_an_entry_into_another_directory_is_neither_dead_nor_duplicated(tmp_path):
    """Cross-linking is deliberate; only genuinely dead links are pruned."""
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": ("# Metric\n\n* [R](revenue.md) - R.\n* [P](../policies/p.md) - See also.\n"),
            "metrics/revenue.md": CONCEPT,
            "policies/p.md": CONCEPT,
            "policies/index.md": "# Policy\n\n* [P](p.md) - P.\n",
        },
    )
    result = plan(loaded, "metrics")
    assert result.dead == ()
    assert result.missing == ()


def test_a_subdirectory_entry_pointing_at_a_missing_index_is_not_dead(tmp_path):
    """A broken *link* is `links.broken`'s business. Pruning it would only make
    the next reconcile add the same directory straight back."""
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n\n* [concepts](concepts/index.md) - The corpus.\n",
            "concepts/a.md": CONCEPT,
        },
    )
    result = plan(loaded, "")
    assert result.dead == ()
    assert result.missing == ()


def test_a_bare_directory_destination_covers_the_subdirectory(tmp_path):
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n\n* [metrics](metrics/) - Numbers.\n",
            "metrics/a.md": CONCEPT,
        },
    )
    result = plan(loaded, "")
    assert result.dead == ()
    assert result.missing == ()


def test_external_and_fragment_only_destinations_are_not_entries(tmp_path):
    loaded = make(
        tmp_path,
        {
            "index.md": (
                "# Subdirectories\n\n"
                "* [spec](https://example.com/spec) - Upstream.\n"
                "* [top](#subdirectories) - Back to the top.\n"
            )
        },
    )
    result = plan(loaded, "")
    assert result.entries == ()
    assert result.dead == ()


def test_reserved_files_are_never_missing_members(tmp_path):
    loaded = make(
        tmp_path,
        {"index.md": "# Subdirectories\n", "log.md": "# Bundle history\n"},
    )
    assert plan(loaded, "").missing == ()


def test_entry_text_is_read_off_the_item(tmp_path):
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": ("# Metric\n\n* [R](revenue.md) - Curated one-liner.\n* [M](margin.md)\n"),
            "metrics/revenue.md": CONCEPT,
            "metrics/margin.md": CONCEPT,
        },
    )
    result = plan(loaded, "metrics")
    assert [entry.text for entry in result.entries] == ["Curated one-liner.", None]
    assert all(entry.prefix is not None for entry in result.entries)


def test_an_unreadable_entry_is_left_strictly_alone(tmp_path):
    """A label containing `]` is legal markdown this cannot cut cleanly."""
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": "# Metric\n\n* [a\\]b](revenue.md) - Text.\n",
            "metrics/revenue.md": CONCEPT,
        },
    )
    (entry,) = plan(loaded, "metrics").entries
    assert entry.prefix is None
    assert entry.text is None


def test_a_destination_containing_a_paren_is_left_strictly_alone(tmp_path):
    """`_LINK_PREFIX_RE` alone would cut at the first `)`, mid-destination.

    markdown-it parses `a(1).md` correctly as the whole destination (a
    balanced, unescaped parenthesis pair is legal CommonMark). The regex's
    naive `[^)]*` would stop early and produce a wrong prefix, so the cut must
    be verified against markdown-it's own parse before it is trusted.
    """
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": "# Metric\n\n* [x](a(1).md) - Text.\n",
            "metrics/a(1).md": CONCEPT,
        },
    )
    (entry,) = plan(loaded, "metrics").entries
    assert entry.prefix is None
    assert entry.text is None


def test_a_directory_containing_only_ignored_content_is_not_proposed(tmp_path):
    """`ignore=` declares content out of scope; a directory holding nothing
    else must not be invented as a missing subdirectory entry."""
    (tmp_path / "index.md").write_text("# Subdirectories\n", encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir()
    (static / "build.log").write_text("noise\n", encoding="utf-8")
    loaded = bundle.load(tmp_path, ignore=["*.log"])
    assert "static/build.log" in loaded.ignored
    result = plan(loaded, "")
    assert result.missing == ()


def test_a_directory_whose_only_content_is_a_nested_subdirectory_is_proposed(tmp_path):
    """'At or below', not just direct children: a subdirectory that is itself
    empty but shelters a concept two levels down must still be proposed."""
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n",
            "static/inner/concept.md": CONCEPT,
        },
    )
    result = plan(loaded, "")
    assert [(t.path, t.kind) for t in result.missing] == [("static", "subdirectory")]


@pytest.mark.parametrize("name", ["acme_retail", "ga4"])
def test_reconciling_a_vendored_bundle_changes_nothing(name, tmp_path):
    """The load-bearing property (spec §9), at the public surface and on disk.

    Runs against a copy, with `dry_run=False`: the point is to prove the write
    path touches nothing, and proving it against the vendored corpus itself
    would risk corrupting it the moment the property does not hold.
    """
    root = tmp_path / name
    shutil.copytree(BUNDLES / name, root)
    before = {path: path.read_bytes() for path in sorted(root.rglob("index.md"))}

    results = index.update(bundle.load(root), dry_run=False)

    assert [r.path for r in results] == sorted(r.path for r in results)
    assert [r.path for r in results] == sorted(before_path.relative_to(root).as_posix() for before_path in before)
    for result in results:
        assert result.changed is False, f"{name}:{result.path}\n{result.diff()}"
        assert result.changes == ()
        assert result.diff() == ""
    assert {path: path.read_bytes() for path in before} == before


def test_adding_a_concept_adds_one_bullet_under_the_right_heading(tmp_path):
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": ("# Metric\n\n* [Revenue](revenue.md) - Recognized revenue.\n"),
            "metrics/revenue.md": CONCEPT,
            "metrics/margin.md": (
                "---\ntype: Metric\ntitle: Gross Margin\ndescription: Margin per policy.\n---\n\n# M\n"
            ),
        },
    )
    (result,) = index.update(loaded, directories=["metrics"])
    assert result.after == (
        "# Metric\n\n* [Revenue](revenue.md) - Recognized revenue.\n* [Gross Margin](margin.md) - Margin per policy.\n"
    )
    assert [(c.kind, c.target) for c in result.changes] == [("add", "metrics/margin.md")]
    assert result.changes[0].heading == "Metric"


def test_a_concept_with_no_description_renders_without_a_separator(tmp_path):
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": "# Metric\n\n* [Revenue](revenue.md) - R.\n",
            "metrics/revenue.md": CONCEPT,
            "metrics/margin.md": "---\ntype: Metric\ntitle: Margin\n---\n\n# M\n",
        },
    )
    (result,) = index.update(loaded, directories=["metrics"])
    assert result.after.endswith("* [Margin](margin.md)\n")


def test_a_concept_with_no_title_falls_back_to_its_filename(tmp_path):
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": "# Metric\n\n* [Revenue](revenue.md) - R.\n",
            "metrics/revenue.md": CONCEPT,
            "metrics/margin.md": "---\ntype: Metric\n---\n\n# M\n",
        },
    )
    (result,) = index.update(loaded, directories=["metrics"])
    assert result.after.endswith("* [margin.md](margin.md)\n")


def test_deleting_a_concept_removes_exactly_its_bullet(tmp_path):
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": ("# Metric\n\n* [Revenue](revenue.md) - R.\n* [Gone](gone.md) - G.\n"),
            "metrics/revenue.md": CONCEPT,
        },
    )
    (result,) = index.update(loaded, directories=["metrics"])
    assert result.after == "# Metric\n\n* [Revenue](revenue.md) - R.\n"
    assert [(c.kind, c.target, c.line) for c in result.changes] == [("remove", "metrics/gone.md", 4)]


def test_prose_is_byte_identical_across_an_add_and_a_remove(tmp_path):
    body = (
        "# Metric\n"
        "\n"
        "These are the numbers Finance signs off on. The wording below is\n"
        "deliberately not the concepts' own `description` -- it is written for\n"
        "someone browsing, not for a machine.\n"
        "\n"
        "* [Revenue](revenue.md) - Recognized revenue.\n"
        "* [Gone](gone.md) - G.\n"
        "\n"
        "<!-- Reviewed 2026-07-01 by kliu@acme -->\n"
    )
    loaded = make(
        tmp_path,
        {
            "metrics/index.md": body,
            "metrics/revenue.md": CONCEPT,
            "metrics/margin.md": ("---\ntype: Metric\ntitle: Margin\ndescription: Margin.\n---\n\n# M\n"),
        },
    )
    (result,) = index.update(loaded, directories=["metrics"])
    kept = [line for line in body.splitlines(keepends=True) if "gone.md" not in line]
    after = result.after.splitlines(keepends=True)
    assert [line for line in after if "margin.md" not in line] == kept
    assert "* [Margin](margin.md) - Margin.\n" in after


def test_a_new_type_gets_its_own_section_at_the_end_of_the_body(tmp_path):
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Revenue](revenue.md) - R.\n",
            "d/revenue.md": CONCEPT,
            "d/p.md": "---\ntype: Policy\ntitle: P\ndescription: A policy.\n---\n\n# P\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after == ("# Metric\n\n* [Revenue](revenue.md) - R.\n\n# Policy\n\n* [P](p.md) - A policy.\n")
    assert result.changes[0].heading == "Policy"


def test_a_typeless_concept_lands_under_concepts(tmp_path):
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [R](revenue.md) - R.\n",
            "d/revenue.md": CONCEPT,
            "d/x.md": "---\ntitle: X\n---\n\n# X\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert "# Concepts\n\n* [X](x.md)\n" in result.after


def test_a_reconciled_index_pointing_at_an_nfd_named_member_via_an_nfc_link_stays_reconciled(tmp_path):
    """An index bullet's destination is written text (NFC, like every other
    reference); the member it names may sit on disk as NFD. Reconciling must
    not read that as "entry missing, member undocumented" -- a false drift
    that would duplicate the bullet on every regeneration."""
    nfd = unicodedata.normalize("NFD", "café")
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Café](caf%C3%A9.md) - The concept.\n",
            f"d/{nfd}.md": '---\ntype: Metric\ntitle: Café\ndescription: "The concept."\n---\n\n# Café\n',
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.changes == ()
    assert result.drift == ()
    assert not result.changed


def test_an_entry_joins_an_existing_but_empty_section(tmp_path):
    """Rule 3 looks for the heading before creating a second copy of it."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n# Policy\n",
            "d/p.md": "---\ntype: Policy\ntitle: P\ndescription: A policy.\n---\n\n# P\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after == "# Metric\n\n# Policy\n* [P](p.md) - A policy.\n"


def test_a_subdirectory_links_to_its_index_when_it_has_one(tmp_path):
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n\n* [a](a/index.md) - A.\n",
            "a/index.md": "# A\n",
            "b/index.md": "# B\n",
            "b/x.md": CONCEPT,
        },
    )
    (result,) = index.update(loaded, directories=[""])
    assert result.after.endswith("* [b](b/index.md)\n")


def test_a_subdirectory_without_an_index_links_to_the_directory(tmp_path):
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n\n* [a](a/index.md) - A.\n",
            "a/index.md": "# A\n",
            "b/x.md": CONCEPT,
        },
    )
    (result,) = index.update(loaded, directories=[""])
    assert result.after.endswith("* [b](b/)\n")


def test_the_bullet_marker_follows_the_file(tmp_path):
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n- [R](revenue.md) - R.\n",
            "d/revenue.md": CONCEPT,
            "d/m.md": "---\ntype: Metric\ntitle: M\n---\n\n# M\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after.endswith("- [M](m.md)\n")


def test_changed_renders_no_diff_and_diff_writes_nothing(tmp_path):
    loaded = make(
        tmp_path,
        {"d/index.md": "# Metric\n", "d/revenue.md": CONCEPT},
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.changed is True
    rendered = result.diff()
    assert rendered.startswith("--- a/d/index.md\n+++ b/d/index.md\n")
    assert "+* [T](revenue.md) - D\n" in rendered
    assert (tmp_path / "d" / "index.md").read_text(encoding="utf-8") == "# Metric\n"


def test_dry_run_is_the_default_and_leaves_the_disk_untouched(tmp_path):
    loaded = make(tmp_path, {"d/index.md": "# Metric\n", "d/revenue.md": CONCEPT})
    index.update(loaded, directories=["d"])
    assert (tmp_path / "d" / "index.md").read_text(encoding="utf-8") == "# Metric\n"


def test_dry_run_false_writes_the_file(tmp_path):
    loaded = make(tmp_path, {"d/index.md": "# Metric\n", "d/revenue.md": CONCEPT})
    (result,) = index.update(loaded, directories=["d"], dry_run=False)
    assert (tmp_path / "d" / "index.md").read_text(encoding="utf-8") == result.after


def test_create_missing_is_off_by_default(tmp_path):
    """§8 says an index MAY appear in any directory; the writer never conjures one."""
    loaded = make(tmp_path, {"d/revenue.md": CONCEPT})
    assert [r.path for r in index.update(loaded, directories=["d"])] == []
    assert not (tmp_path / "d" / "index.md").exists()


def test_create_missing_creates_an_index_with_no_frontmatter(tmp_path):
    loaded = make(tmp_path, {"d/revenue.md": CONCEPT})
    (result,) = index.update(loaded, directories=["d"], create_missing=True, dry_run=False)
    assert result.created is True
    assert result.before == ""
    written = (tmp_path / "d" / "index.md").read_text(encoding="utf-8")
    assert written == "# Metric\n\n* [T](revenue.md) - D\n"
    assert not written.startswith("---")


def test_directories_none_covers_every_directory(tmp_path):
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n\n* [d](d/index.md) - D.\n",
            "d/index.md": "# Metric\n",
            "d/revenue.md": CONCEPT,
        },
    )
    assert [r.path for r in index.update(loaded)] == ["d/index.md", "index.md"]


def _dot_subtree(tmp_path: Path) -> bundle.Bundle:
    return make(
        tmp_path,
        {
            "concepts/m.md": CONCEPT,
            "repositories/demo/.agents/skills/SKILL.md.md": CONCEPT,
        },
    )


def test_auto_selection_never_conjures_an_index_inside_a_dot_subtree(tmp_path):
    """The orphan `create_missing` path. `_subdirectories_of` will not propose
    a dot-directory as an entry in its parent, so an index auto-created inside
    one could never be linked from anywhere -- ADR-0028 made the directory a
    real member, not a curated one. `.agents/skills` has to go too: it is
    reachable only through the `.agents` already declined.
    """
    paths = [r.path for r in index.update(_dot_subtree(tmp_path), create_missing=True)]
    assert paths == ["concepts/index.md", "index.md", "repositories/demo/index.md", "repositories/index.md"]


def test_naming_a_dot_directory_outright_still_creates_its_index(tmp_path):
    """Declining is about auto-selection, not about the directory. A caller
    who names one is as entitled to an index as `create_missing` itself is."""
    (result,) = index.update(_dot_subtree(tmp_path), directories=["repositories/demo/.agents"], create_missing=True)
    assert (result.path, result.created) == ("repositories/demo/.agents/index.md", True)


def test_an_index_already_inside_a_dot_subtree_is_still_reconciled(tmp_path):
    """Reconciling what exists is untouched: only creation is declined."""
    loaded = make(
        tmp_path,
        {
            "repositories/demo/.agents/index.md": "# Metric\n",
            "repositories/demo/.agents/note.md": CONCEPT,
        },
    )
    paths = [r.path for r in index.update(loaded)]
    assert "repositories/demo/.agents/index.md" in paths


def test_an_unknown_directory_raises(tmp_path):
    loaded = make(tmp_path, {"index.md": "# Subdirectories\n"})
    with pytest.raises(ValueError, match="not a directory"):
        index.update(loaded, directories=["nope"])


# --- Regression: a new section anchored past a removal that eats the tail ---
#
# `_update_one` used to anchor a brand-new section's insertion on the body's
# raw last line (`_edit.line_count(body)`), with no regard for whether a
# queued removal also covers that line. When it did, the insertion and the
# removal collided: `_edit.apply` either refused outright (no trailing
# newline) or silently accepted an insertion that landed on the wrong side of
# the deletion, doubling a blank line forever. `_last_surviving_line` fixes
# this by anchoring on the line that will actually be left standing.


def test_a_new_section_survives_a_dead_last_line_with_no_trailing_newline(tmp_path):
    """Critical 1: a dead entry as the file's literal last line, no trailing
    newline, used to raise `ValueError: Overlapping edit` instead of
    reconciling."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Gone](gone.md) - G.",
            "d/p.md": "---\ntype: Policy\ntitle: P\ndescription: A policy.\n---\n\n# P\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after == "# Metric\n\n# Policy\n\n* [P](p.md) - A policy.\n"


def test_a_new_section_does_not_double_the_blank_line_after_a_dead_last_line(tmp_path):
    """Critical 2: the same shape, but the dead line carries its own trailing
    newline -- so nothing raises, but the old anchoring produced a doubled
    blank line that no later reconcile would ever repair. This asserts the
    exact text, then reconciles the *result* again and requires that second
    pass to report no change: proof the corruption is gone, not relocated."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Gone](gone.md) - G.\n",
            "d/p.md": "---\ntype: Policy\ntitle: P\ndescription: A policy.\n---\n\n# P\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after == "# Metric\n\n# Policy\n\n* [P](p.md) - A policy.\n"

    (tmp_path / "d" / "index.md").write_text(result.after, encoding="utf-8")
    (again,) = index.update(bundle.load(tmp_path), directories=["d"])
    assert again.changed is False
    assert again.changes == ()


def test_a_new_entry_under_an_emptied_heading_keeps_the_blank_line_convention(tmp_path):
    """Important 3: a heading whose only entry was just pruned still owns the
    blank line separating it from what follows; a new sibling belongs after
    that blank line, not glued to the heading."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Gone](gone.md) - G.\n",
            "d/new.md": "---\ntype: Metric\ntitle: New\ndescription: N.\n---\n\n# N\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after == "# Metric\n\n* [New](new.md) - N.\n"


def test_missing_entry_replaces_an_emptied_headingless_section(tmp_path):
    """`_anchor_for`'s `key is None` path: entries sitting under no heading at
    all, every one of them dead -- there is nothing to anchor a survivor on,
    so a titled section is created instead."""
    loaded = make(
        tmp_path,
        {"d/index.md": "* [Gone](gone.md) - G.\n", "d/x.md": "---\ntitle: X\n---\n\n# X\n"},
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after == "# Concepts\n\n* [X](x.md)\n"


def test_a_missing_subdirectory_creates_a_subdirectories_section_from_scratch(tmp_path):
    """`_new_section_title`'s subdirectory branch, with no `# Subdirectories`
    heading anywhere in the file to find."""
    loaded = make(tmp_path, {"index.md": "# Concepts\n", "sub/x.md": CONCEPT})
    (result,) = index.update(loaded, directories=[""])
    assert result.after == "# Concepts\n\n# Subdirectories\n\n* [sub](sub/)\n"


def test_two_new_entries_merge_into_the_same_freshly_created_section(tmp_path):
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Revenue](revenue.md) - R.\n",
            "d/revenue.md": CONCEPT,
            "d/a.md": "---\ntype: Policy\ntitle: A\ndescription: A policy.\n---\n\n# A\n",
            "d/b.md": "---\ntype: Policy\ntitle: B\ndescription: B policy.\n---\n\n# B\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"])
    assert result.after == (
        "# Metric\n\n* [Revenue](revenue.md) - R.\n\n# Policy\n\n* [A](a.md) - A policy.\n* [B](b.md) - B policy.\n"
    )


def test_describe_overrides_the_concept_description_on_add(tmp_path):
    """`describe=` is extension point #4, live on the addition path."""
    loaded = make(tmp_path, {"d/index.md": "# Metric\n", "d/revenue.md": CONCEPT})
    (result,) = index.update(loaded, directories=["d"], describe=lambda target: f"custom:{target.path}")
    assert result.after == "# Metric\n* [T](revenue.md) - custom:d/revenue.md\n"


def test_drift_is_reported_without_being_written():
    """acme_retail's index text deliberately differs from the concepts' own."""
    loaded = bundle.load(BUNDLES / "acme_retail")
    (result,) = index.update(loaded, directories=["metrics"])
    assert result.changed is False
    assert {d.target for d in result.drift} == {
        "metrics/revenue.md",
        "metrics/gross-margin.md",
        "metrics/gross-margin-legacy.md",
    }
    drifted = next(d for d in result.drift if d.target == "metrics/revenue.md")
    assert drifted.text == "Recognized revenue per Acme's FY2026 policy."
    assert drifted.description.startswith("Recognized revenue for a period")


def test_a_verbatim_index_reports_no_drift():
    loaded = bundle.load(BUNDLES / "ga4")
    (result,) = index.update(loaded, directories=["references/metrics"])
    assert result.drift == ()


def test_refresh_rewrites_and_reports_every_rewrite(tmp_path):
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Revenue](revenue.md) - Curated.\n",
            "d/revenue.md": ("---\ntype: Metric\ntitle: Revenue\ndescription: From frontmatter.\n---\n\n# R\n"),
        },
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.after == "# Metric\n\n* [Revenue](revenue.md) - From frontmatter.\n"
    assert [(c.kind, c.target, c.line) for c in result.changes] == [("refresh", "d/revenue.md", 3)]
    assert result.drift == ()


def test_refresh_preserves_the_destination_as_written(tmp_path):
    """markdown-it percent-encodes destinations on the way out; a refresh must
    not write that normalization back into the file."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n  - [Café](./café.md) - Old.\n",
            "d/café.md": "---\ntype: Metric\ntitle: Café\ndescription: New.\n---\n\n# C\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.after == "# Metric\n\n  - [Café](./café.md) - New.\n"


def test_refresh_leaves_an_entry_with_no_computed_description_alone(tmp_path):
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [R](revenue.md) - Human wrote this.\n",
            "d/revenue.md": "---\ntype: Metric\ntitle: R\n---\n\n# R\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.changed is False
    assert result.changes == ()


def test_refresh_leaves_an_unreadable_entry_alone(tmp_path):
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [a\\]b](revenue.md) - Old.\n",
            "d/revenue.md": ("---\ntype: Metric\ntitle: R\ndescription: New.\n---\n\n# R\n"),
        },
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.changed is False


def test_a_subdirectory_entry_is_never_drift(tmp_path):
    """A directory has no `description` to drift from."""
    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n\n* [d](d/index.md) - Written by hand.\n",
            "d/index.md": "# Metric\n",
        },
    )
    (result,) = index.update(loaded, directories=[""])
    assert result.drift == ()


def test_describe_supplies_text_for_a_new_entry_from_outside_the_core(tmp_path):
    """Extension point #4, exercised from outside okf-io."""
    seen: list[tuple[str, str]] = []

    def describe(target: index.EntryTarget) -> str | None:
        seen.append((target.path, target.kind))
        if target.kind == "subdirectory":
            return "A directory, described elsewhere."
        return f"Schema summary for {PurePosixPath(target.path).stem}."

    loaded = make(
        tmp_path,
        {
            "index.md": "# Subdirectories\n",
            "d/index.md": "# Metric\n",
            "d/revenue.md": CONCEPT,
        },
    )
    results = index.update(loaded, describe=describe)
    root = next(r for r in results if r.path == "index.md")
    inner = next(r for r in results if r.path == "d/index.md")
    assert root.after.endswith("* [d](d/index.md) - A directory, described elsewhere.\n")
    assert inner.after.endswith("* [T](revenue.md) - Schema summary for revenue.\n")
    assert ("d", "subdirectory") in seen
    assert ("d/revenue.md", "concept") in seen


def test_describe_returning_none_means_no_description(tmp_path):
    loaded = make(tmp_path, {"d/index.md": "# Metric\n", "d/revenue.md": CONCEPT})
    (result,) = index.update(loaded, directories=["d"], describe=lambda target: None)
    assert result.after.endswith("* [T](revenue.md)\n")


def test_describe_receives_the_document_when_there_is_one(tmp_path):
    captured: list[index.EntryTarget] = []
    loaded = make(tmp_path, {"d/index.md": "# Metric\n", "d/revenue.md": CONCEPT})
    index.update(loaded, directories=["d"], describe=captured.append)
    assert captured[0].document is not None
    assert captured[0].document.fm.title == "T"


def test_describe_is_not_consulted_for_drift_under_preserve(tmp_path):
    """§6.1 calls `describe` for entries about to be *added*, and for every
    entry under `refresh`. Drift is about a concept's own `description`."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [T](revenue.md) - D\n",
            "d/revenue.md": CONCEPT,
        },
    )
    (result,) = index.update(loaded, directories=["d"], describe=lambda target: "Something else entirely.")
    assert result.drift == ()
    assert result.changed is False


def test_describe_drives_a_refresh(tmp_path):
    loaded = make(
        tmp_path,
        {"d/index.md": "# Metric\n\n* [R](revenue.md) - Old.\n", "d/revenue.md": CONCEPT},
    )
    (result,) = index.update(
        loaded,
        directories=["d"],
        descriptions="refresh",
        describe=lambda target: "From the schema registry.",
    )
    assert result.after == "# Metric\n\n* [R](revenue.md) - From the schema registry.\n"


# --- Code review fixes: two Critical defects and two Important ones, all in
# the plan's prescribed `_refresh_and_drift` (lines 2143-2157), not the
# transcription. ---


def test_refresh_leaves_a_wrapped_entry_alone(tmp_path):
    """A bullet whose text continues onto a second physical line cannot be
    rewritten without deciding what happens to the continuation. Guessing
    risks a subtler corruption than the one this guards against, so a
    wrapped entry is left alone -- exactly like an unreadable one -- and
    never reported as drift either."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": (
                "# Metric\n\n* [Revenue](revenue.md) - Old text that\n  continues on a second physical line.\n"
            ),
            "d/revenue.md": ("---\ntype: Metric\ntitle: Revenue\ndescription: New description.\n---\n\n# R\n"),
        },
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.changed is False
    assert result.changes == ()
    assert result.drift == ()


def test_refresh_and_a_new_section_survive_a_newline_less_last_line(tmp_path):
    """A refresh on the body's literal last line, which carries no trailing
    newline, used to collide with `insert_after`'s own newline-correction
    edit for a brand-new section -- both landed on the same line range and
    `_edit.apply` raised `Overlapping edit`."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Revenue](revenue.md) - Old.",
            "d/revenue.md": ("---\ntype: Metric\ntitle: Revenue\ndescription: New.\n---\n\n# R\n"),
            "d/p.md": "---\ntype: Policy\ntitle: P\ndescription: A policy.\n---\n\n# P\n",
        },
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.after == ("# Metric\n\n* [Revenue](revenue.md) - New.\n\n# Policy\n\n* [P](p.md) - A policy.\n")


def test_refresh_and_a_sibling_addition_survive_a_newline_less_last_line(tmp_path):
    """Same collision as the new-section case, but the addition joins an
    *existing* section instead of creating one -- `by_anchor`'s insertion,
    not `_last_surviving_line`'s. The anchor for a same-section sibling is
    the last surviving item under that heading, which is exactly the
    refreshed, newline-less last line here."""
    loaded = make(
        tmp_path,
        {
            "d/index.md": "# Metric\n\n* [Revenue](revenue.md) - Old.",
            "d/revenue.md": ("---\ntype: Metric\ntitle: Revenue\ndescription: New.\n---\n\n# R\n"),
            "d/margin.md": ("---\ntype: Metric\ntitle: Margin\ndescription: A margin.\n---\n\n# M\n"),
        },
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.after == ("# Metric\n\n* [Revenue](revenue.md) - New.\n* [Margin](margin.md) - A margin.\n")


def test_refresh_change_reports_the_heading_verbatim(tmp_path):
    """`IndexChange.heading` is documented as verbatim, matching what `add`
    and `remove` already produce via `_heading_text` -- not the casefolded
    parser key that `ListItem.heading` carries."""
    loaded = make(
        tmp_path,
        {"d/index.md": "# Metric\n\n* [R](revenue.md) - Old.\n", "d/revenue.md": CONCEPT},
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh")
    assert result.changes == (
        index.IndexChange(
            kind="refresh",
            target="d/revenue.md",
            heading="Metric",
            text="* [R](revenue.md) - D",
            line=3,
        ),
    )


def test_describe_returning_empty_string_produces_no_dangling_separator(tmp_path):
    """The add path renders through `_render`, which degrades a falsy
    description to no separator; the hand-built refresh bullet must match."""
    loaded = make(
        tmp_path,
        {"d/index.md": "# Metric\n\n* [R](revenue.md) - Old.\n", "d/revenue.md": CONCEPT},
    )
    (result,) = index.update(loaded, directories=["d"], descriptions="refresh", describe=lambda target: "")
    assert result.after == "# Metric\n\n* [R](revenue.md)\n"


def test_refresh_is_idempotent_on_a_vendored_bundle(tmp_path):
    """First pass rewrites acme_retail's curated one-liners under refresh;
    reconciling the rewritten bundle again reports no further change."""
    root = tmp_path / "acme_retail"
    shutil.copytree(BUNDLES / "acme_retail", root)

    first = index.update(bundle.load(root), descriptions="refresh", dry_run=False)
    assert any(result.changed for result in first)

    second = index.update(bundle.load(root), descriptions="refresh")
    assert all(result.changed is False for result in second)
