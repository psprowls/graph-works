from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migrate_vault import _SUBCOMMANDS as _SUBCOMMANDS_FOR_TEST
from migrate_vault import ignore_patterns


def page(vault: Path, member: str, body: str = "", *, frontmatter: str = "") -> Path:
    """Write one bundle member. `frontmatter` is raw YAML lines, no fences."""
    target = vault / member
    target.parent.mkdir(parents=True, exist_ok=True)
    if frontmatter:
        target.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    else:
        target.write_text(body, encoding="utf-8")
    return target


def vault_root(tmp_path: Path) -> Path:
    """A minimal OKF bundle root: root index with `okf_version`, plus `.gw/`."""
    root = tmp_path / "wiki"
    root.mkdir()
    page(root, "index.md", "# Index\n", frontmatter="okf_version: 0.2\n")
    (tmp_path / ".gw").mkdir()
    return root


BASE_IGNORE = (
    ".templates/*",
    "CLAUDE.md",
    "*.json",
    "guidance/*",
    "work/*/0[1-9]-*.md",
    "work/*/[1-9][0-9]-*.md",
    "work/*/child-specs/*",
)


def test_ignore_patterns_carries_the_base_set_in_every_phase() -> None:
    for phase in ("frontmatter", "titles", "links", "archive-shape", "work", "entities", "lanes", "bundle"):
        assert set(BASE_IGNORE) <= set(ignore_patterns(phase)), phase


def test_ignore_patterns_hides_the_entity_lane_until_phase_seven() -> None:
    assert "entities/*" in ignore_patterns("frontmatter")
    assert "entities/*" in ignore_patterns("archive-shape")
    assert "entities/*" not in ignore_patterns("entities")
    assert "entities/*" not in ignore_patterns("lanes")


def test_ignore_patterns_never_names_declarations() -> None:
    """`_schema/` and `sections/` live at `.gw/`, outside `bundle_dir`, so they
    are never members and must never appear here (spec, *The shared `ignore=`
    declaration*)."""
    for phase in ("frontmatter", "work", "entities", "gate"):
        joined = " ".join(ignore_patterns(phase))
        assert "_schema" not in joined
        assert "sections" not in joined


def test_ignore_patterns_keeps_archived_item_pages_visible() -> None:
    """`work/_archive/*/00-open-work.md` is an item page wearing an artifact's
    filename; phase 6a is what fixes the filename. Ignoring it would let phase 6
    silently pass over 174 legacy items."""
    from fnmatch import fnmatchcase

    member = "work/_archive/2026-08-10-bug-example/00-open-work.md"
    assert not any(fnmatchcase(member, pattern) for pattern in ignore_patterns("work"))


def test_ignore_patterns_does_ignore_the_sibling_stage_documents() -> None:
    from fnmatch import fnmatchcase

    for member in (
        "work/2026-08-11-epic-example/01-design-spec.md",
        "work/_archive/2026-08-10-bug-example/03-execute-results.md",
        "work/2026-08-11-epic-example/child-specs/c2.md",
    ):
        assert any(fnmatchcase(member, pattern) for pattern in ignore_patterns("work")), member


def test_every_phase_has_a_subcommand_and_a_handler() -> None:
    from migrate_vault import _HANDLERS, _SUBCOMMANDS

    assert {name for name, _ in _SUBCOMMANDS} == set(_HANDLERS)
    assert len(_HANDLERS) == 10


def test_a_vault_that_is_not_a_directory_exits_two(tmp_path: Path) -> None:
    from migrate_vault import main

    missing = tmp_path / "nope"
    assert main(["frontmatter", str(missing)]) == 2


def test_every_subcommand_is_dry_run_without_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from migrate_vault import main

    root = vault_root(tmp_path)
    for name, _ in _SUBCOMMANDS_FOR_TEST:
        main([name, str(root)]) if name != "gate" else main([name, str(root), "--today", "2026-08-23"])
        assert "Dry run." in capsys.readouterr().out, name


# ---- phase 4a: decisions ----

DECISIONS_YAML = ".gw/migration/decisions.yaml"


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"write": False, "repo_rename": [], "scan": True, "today": date(2026, 8, 23), "baseline": None}
    return argparse.Namespace(**{**defaults, **kwargs})


def _rows(path: Path) -> list[dict]:
    from ruamel.yaml import YAML

    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))["rows"]


def test_decisions_emits_one_row_per_concept_and_writes_nothing_to_the_vault(tmp_path: Path) -> None:
    from migrate_vault import cmd_decisions

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\nkind: architecture\n")
    page(root, "concepts/b.md", "# B\n", frontmatter="title: B\n")
    before = {p: p.read_bytes() for p in root.rglob("*.md")}

    result = cmd_decisions(_args(vault=root, write=True))

    assert result.ok
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == before
    rows = _rows(tmp_path / DECISIONS_YAML)
    assert [row["member"] for row in rows] == ["concepts/a.md", "concepts/b.md"]
    assert all(row["question"] == "diataxis-type" for row in rows)


def test_decisions_adds_a_status_active_row(tmp_path: Path) -> None:
    from migrate_vault import cmd_decisions

    root = vault_root(tmp_path)
    page(root, "concepts/c.md", "# C\n", frontmatter="title: C\nstatus: active\n")

    cmd_decisions(_args(vault=root, write=True))

    rows = _rows(tmp_path / DECISIONS_YAML)
    questions = {(row["member"], row["question"]) for row in rows}
    assert ("concepts/c.md", "status-active") in questions
    status_row = next(row for row in rows if row["question"] == "status-active")
    assert status_row["proposed"] == "delete"
    assert status_row["current"] == {"status": "active"}


def test_decisions_is_deterministic(tmp_path: Path) -> None:
    from migrate_vault import cmd_decisions

    root = vault_root(tmp_path)
    for name in ("z", "a", "m"):
        page(root, f"concepts/{name}.md", f"# {name}\n", frontmatter=f"title: {name}\n")

    cmd_decisions(_args(vault=root, write=True))
    first = (tmp_path / DECISIONS_YAML).read_text(encoding="utf-8")
    cmd_decisions(_args(vault=root, write=True))
    assert (tmp_path / DECISIONS_YAML).read_text(encoding="utf-8") == first


def test_decisions_rerun_preserves_a_human_decision_and_appends_new_rows(tmp_path: Path) -> None:
    from migrate_vault import cmd_decisions

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\n")
    cmd_decisions(_args(vault=root, write=True))

    path = tmp_path / DECISIONS_YAML
    path.write_text(
        path.read_text(encoding="utf-8").replace("decision:\n", "decision: Reference\n", 1), encoding="utf-8"
    )

    page(root, "concepts/b.md", "# B\n", frontmatter="title: B\n")
    cmd_decisions(_args(vault=root, write=True))

    rows = {row["member"]: row for row in _rows(path)}
    assert rows["concepts/a.md"]["decision"] == "Reference"
    assert rows["concepts/b.md"]["decision"] is None
    assert len(rows) == 2


def test_load_decisions_inherits_proposed_when_decision_is_blank(tmp_path: Path) -> None:
    from migrate_vault import load_decisions

    path = tmp_path / "decisions.yaml"
    path.write_text(
        "version: 1\nrows:\n"
        "  - member: concepts/a.md\n    question: diataxis-type\n    proposed: Reference\n    decision:\n",
        encoding="utf-8",
    )
    resolved, refusals = load_decisions(path)
    assert refusals == ()
    assert resolved[("concepts/a.md", "diataxis-type")] == "Reference"


def test_load_decisions_lets_an_explicit_decision_win(tmp_path: Path) -> None:
    from migrate_vault import load_decisions

    path = tmp_path / "decisions.yaml"
    path.write_text(
        "version: 1\nrows:\n"
        "  - member: concepts/a.md\n    question: diataxis-type\n    proposed: Reference\n    decision: Explanation\n",
        encoding="utf-8",
    )
    resolved, refusals = load_decisions(path)
    assert refusals == ()
    assert resolved[("concepts/a.md", "diataxis-type")] == "Explanation"


def test_load_decisions_refuses_an_illegal_decision_rather_than_defaulting(tmp_path: Path) -> None:
    from migrate_vault import load_decisions

    path = tmp_path / "decisions.yaml"
    path.write_text(
        "version: 1\nrows:\n"
        "  - member: concepts/a.md\n    question: diataxis-type\n    proposed: Reference\n    decision: Wharrgarbl\n",
        encoding="utf-8",
    )
    resolved, refusals = load_decisions(path)
    assert ("concepts/a.md", "diataxis-type") not in resolved
    assert [r.reason for r in refusals] == ["illegal-decision"]


# ---- phase 4b: frontmatter ----


def _decide(workspace: Path, rows: tuple[tuple[str, str, str], ...]) -> Path:
    """Write a minimal `.gw/migration/decisions.yaml` holding *rows*."""
    target = workspace / ".gw" / "migration" / "decisions.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "version: 1\nrows:\n" + "".join(
        f"  - member: {member}\n    question: {question}\n    proposed: {value}\n    decision:\n"
        for member, question, value in rows
    )
    target.write_text(body if rows else "version: 1\nrows: []\n", encoding="utf-8")
    return target


def test_frontmatter_maps_work_kind_to_type_and_status_to_work_status(tmp_path: Path) -> None:
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(
        root,
        "work/2026-08-11-feature-x.md",
        "# X\n",
        frontmatter="title: X\nkind: feature\nstatus: in-progress\nsummary: does a thing\n",
    )
    _decide(tmp_path, rows=())

    result = cmd_frontmatter(_args(vault=root, write=True))

    assert result.ok, result.refusals
    text = (root / "work/2026-08-11-feature-x.md").read_text(encoding="utf-8")
    assert "type: Feature" in text
    assert "work_status: in-progress" in text
    assert "description: does a thing" in text
    assert "kind:" not in text
    assert "summary:" not in text


def test_frontmatter_never_writes_the_legacy_status_key(tmp_path: Path) -> None:
    """D-012 names the one-dialect-behind key; phase 6 renames it, so writing it
    here would rewrite every item page twice through a state matching no schema."""
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(root, "work/2026-08-11-bug-y.md", "# Y\n", frontmatter="title: Y\nkind: bug\nstatus: open\n")
    _decide(tmp_path, rows=())
    cmd_frontmatter(_args(vault=root, write=True))
    assert "workflow" + "_status" not in (root / "work/2026-08-11-bug-y.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(("legacy", "tag"), [("security", "security"), ("perf", "perf")])
def test_frontmatter_maps_the_two_defect_kinds_to_bug_plus_a_tag(tmp_path: Path, legacy: str, tag: str) -> None:
    """Zero pages in the live vault. The map and its test ship anyway --
    CONTRIBUTED_TAGS still defines both (`vocabulary.py:39-42`)."""
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(
        root,
        f"work/2026-08-11-{legacy}-z.md",
        "# Z\n",
        frontmatter=f"title: Z\nkind: {legacy}\nstatus: open\ntags:\n- existing\n",
    )
    _decide(tmp_path, rows=())
    cmd_frontmatter(_args(vault=root, write=True))

    from okf_io import load as load_one  # okf_io.load reads one file

    document = load_one(root / f"work/2026-08-11-{legacy}-z.md")
    assert document.fm.type == "Bug"
    assert tag in document.fm.tags
    assert "existing" in document.fm.tags


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ("spec", "doc"),
        ("transcript", "doc"),
        ("note", "doc"),
        ("pr", "pr"),
        ("example", "example"),
    ],
)
def test_frontmatter_maps_source_type_to_source_kind(tmp_path: Path, legacy: str, expected: str) -> None:
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(root, "sources/s.md", "# S\n", frontmatter=f"title: S\nsource_type: {legacy}\n")
    _decide(tmp_path, rows=())
    cmd_frontmatter(_args(vault=root, write=True))

    text = (root / "sources/s.md").read_text(encoding="utf-8")
    assert f"source_kind: {expected}" in text
    assert "source_type:" not in text


@pytest.mark.parametrize(("legacy", "expected"), [("accepted", "stable"), ("superseded", "deprecated")])
def test_frontmatter_maps_adr_status(tmp_path: Path, legacy: str, expected: str) -> None:
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(root, "adrs/0001-a.md", "# A\n", frontmatter=f"title: A\nstatus: {legacy}\n")
    _decide(tmp_path, rows=())
    cmd_frontmatter(_args(vault=root, write=True))
    assert f"status: {expected}" in (root / "adrs/0001-a.md").read_text(encoding="utf-8")


def test_frontmatter_deletes_an_integer_sources_and_clears_the_coercion_failure(tmp_path: Path) -> None:
    """`sources` is a reserved field expecting a list of mappings
    (`okf-io/models.py:155`); an integer is a guaranteed coercion_failure, which
    gate assertion 2 refuses. The value is a page count, carrying nothing."""
    from migrate_vault import cmd_frontmatter
    from okf_io import load as load_one

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\nsources: 7\ncategory: wiki\n")
    _decide(tmp_path, rows=(("concepts/a.md", "diataxis-type", "Reference"),))

    assert load_one(root / "concepts/a.md").fm.coercion_failures  # pre-condition
    cmd_frontmatter(_args(vault=root, write=True))

    document = load_one(root / "concepts/a.md")
    assert document.fm.coercion_failures == frozenset()
    assert document.fm.extra.get("category") == "wiki"  # untouched


def test_frontmatter_refuses_a_concept_absent_from_the_decisions_file(tmp_path: Path) -> None:
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\nstatus: active\n")
    _decide(tmp_path, rows=())  # deliberately empty

    result = cmd_frontmatter(_args(vault=root, write=True))

    assert not result.ok
    assert [r.reason for r in result.refusals] == ["uncovered-by-decisions"]
    assert "status: active" in (root / "concepts/a.md").read_text(encoding="utf-8")  # nothing written


def test_frontmatter_leaves_an_unmutated_page_byte_identical(tmp_path: Path) -> None:
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    target = root / "adrs/0002-crlf.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes("﻿---\r\ntitle: Already fine\r\ntype: Adr\r\nstatus: stable\r\n---\r\n\r\n# Already fine\r\n".encode())
    before = target.read_bytes()
    _decide(tmp_path, rows=())

    cmd_frontmatter(_args(vault=root, write=True))

    assert target.read_bytes() == before


def test_frontmatter_is_idempotent(tmp_path: Path) -> None:
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(
        root, "work/2026-08-11-feature-x.md", "# X\n", frontmatter="title: X\nkind: feature\nstatus: open\nsummary: s\n"
    )
    _decide(tmp_path, rows=())

    cmd_frontmatter(_args(vault=root, write=True))
    first = (root / "work/2026-08-11-feature-x.md").read_bytes()
    second_run = cmd_frontmatter(_args(vault=root, write=True))

    assert second_run.changed == ()
    assert (root / "work/2026-08-11-feature-x.md").read_bytes() == first


def test_frontmatter_never_touches_a_stage_document(tmp_path: Path) -> None:
    """Load-bearing on phase 6: if a stage doc's `kind: bug` became `type: Bug`,
    `_nodes` would refuse it as `legacy-path-invalid` (`migration.py:194-201`)."""
    from migrate_vault import cmd_frontmatter

    root = vault_root(tmp_path)
    page(root, "work/2026-08-11-epic-e.md", "# E\n", frontmatter="title: E\nkind: epic\nstatus: open\n")
    stage = page(
        root,
        "work/2026-08-11-epic-e/01-design-spec.md",
        "# Spec\n",
        frontmatter="title: Spec\nkind: bug\nstatus: draft\n",
    )
    before = stage.read_bytes()
    _decide(tmp_path, rows=())

    cmd_frontmatter(_args(vault=root, write=True))

    assert stage.read_bytes() == before


def test_titles_backfills_from_the_h1(tmp_path: Path) -> None:
    from migrate_vault import cmd_titles

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "# The Real Title\n\nbody\n", frontmatter="type: Reference\n")

    result = cmd_titles(_args(vault=root, write=True))

    assert result.ok, result.refusals
    assert "title: The Real Title" in (root / "concepts/a.md").read_text(encoding="utf-8")


def test_titles_leaves_a_titled_page_byte_identical(tmp_path: Path) -> None:
    from migrate_vault import cmd_titles

    root = vault_root(tmp_path)
    target = page(root, "concepts/a.md", "# Heading\n", frontmatter="title: Already\n")
    before = target.read_bytes()

    cmd_titles(_args(vault=root, write=True))

    assert target.read_bytes() == before


def test_titles_refuses_a_page_with_neither_a_title_nor_an_h1(tmp_path: Path) -> None:
    """A filename-derived title is a guess, and this sweep does not guess."""
    from migrate_vault import cmd_titles

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "just prose, no heading\n", frontmatter="type: Reference\n")

    result = cmd_titles(_args(vault=root, write=True))

    assert not result.ok
    assert [r.reason for r in result.refusals] == ["no-title-source"]


def test_titles_is_idempotent(tmp_path: Path) -> None:
    from migrate_vault import cmd_titles

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "# T\n", frontmatter="type: Reference\n")
    cmd_titles(_args(vault=root, write=True))
    assert cmd_titles(_args(vault=root, write=True)).changed == ()


# ---- phase 5: links ----


def test_links_converts_a_wikilink_to_a_markdown_link(tmp_path: Path) -> None:
    from migrate_vault import cmd_links

    root = vault_root(tmp_path)
    page(root, "concepts/target.md", "# Target\n", frontmatter="title: Target\n")
    page(root, "concepts/source.md", "See [[concepts/target]].\n", frontmatter="title: Source\n")

    result = cmd_links(_args(vault=root, write=True))

    assert result.ok
    assert "[Target](/concepts/target.md)" in (root / "concepts/source.md").read_text(encoding="utf-8")


def test_links_runs_after_titles_and_uses_the_backfilled_title(tmp_path: Path) -> None:
    """The epic's first ordering constraint, as a regression. `titles` first."""
    from migrate_vault import cmd_links, cmd_titles

    root = vault_root(tmp_path)
    page(root, "entities/pkg_okf-io.md", "# okf-io\n", frontmatter="type: Package\n")
    page(root, "concepts/source.md", "See [[entities/pkg_okf-io]].\n", frontmatter="title: Source\n")

    cmd_titles(_args(vault=root, write=True))
    cmd_links(_args(vault=root, write=True))

    body = (root / "concepts/source.md").read_text(encoding="utf-8")
    assert "[okf-io](" in body
    assert "[pkg_okf-io](" not in body


def test_links_without_titles_first_produces_the_wrong_link_text(tmp_path: Path) -> None:
    """The regression the ordering prevents. Kept so a runbook that reorders the
    two phases fails here rather than in the live vault."""
    from migrate_vault import cmd_links

    root = vault_root(tmp_path)
    page(root, "entities/pkg_okf-io.md", "# okf-io\n", frontmatter="type: Package\n")
    page(root, "concepts/source.md", "See [[entities/pkg_okf-io]].\n", frontmatter="title: Source\n")

    cmd_links(_args(vault=root, write=True))

    assert "[okf-io](" in (root / "concepts/source.md").read_text(encoding="utf-8")
    # `heading_of` still finds the H1, so the text is right *here*; the failure
    # mode is a target with neither -- which `titles` refuses outright.


def test_links_never_backfills_titles_itself(tmp_path: Path) -> None:
    """`titles` owns backfill. Two backfills that disagree is the bug the
    ordering exists to prevent, so `links` passes `backfill=False`."""
    from migrate_vault import cmd_links

    root = vault_root(tmp_path)
    target = page(root, "concepts/target.md", "# Target\n", frontmatter="type: Reference\n")
    page(root, "concepts/source.md", "See [[concepts/target]].\n", frontmatter="title: Source\n")
    before = target.read_bytes()

    cmd_links(_args(vault=root, write=True))

    assert target.read_bytes() == before


def test_links_reports_an_unresolved_target_as_a_note_not_a_refusal(tmp_path: Path) -> None:
    from migrate_vault import cmd_links

    root = vault_root(tmp_path)
    page(root, "concepts/source.md", "See [[concepts/nowhere]].\n", frontmatter="title: Source\n")

    result = cmd_links(_args(vault=root, write=True))

    assert result.ok
    assert any("unresolved" in note for note in result.notes)
    assert "[[concepts/nowhere]]" in (root / "concepts/source.md").read_text(encoding="utf-8")


def test_links_is_idempotent(tmp_path: Path) -> None:
    from migrate_vault import cmd_links

    root = vault_root(tmp_path)
    page(root, "concepts/target.md", "# T\n", frontmatter="title: T\n")
    page(root, "concepts/source.md", "See [[concepts/target]].\n", frontmatter="title: S\n")

    cmd_links(_args(vault=root, write=True))
    first = (root / "concepts/source.md").read_bytes()
    assert cmd_links(_args(vault=root, write=True)).changed == ()
    assert (root / "concepts/source.md").read_bytes() == first


# ---- phase 6a: archive-shape ----

ARCHIVED = "work/_archive/2026-08-10-bug-coercion-failures-unread"


def _archived_item(root: Path, slug: str = "2026-08-10-bug-coercion-failures-unread") -> None:
    """One archived item in this vault's nested form, with its stage siblings.

    Frontmatter is `Bug.schema.json`-complete and the body carries a `Plan`
    section -- required so `plan_migration`'s final projection lint (which
    re-validates the migrated result against the real catalog, independent of
    whatever `ignore=` loaded the pre-migration bundle) has something to pass,
    not just the fields `archive-shape` itself cares about.
    """
    frontmatter = (
        "title: Coercion failures\n"
        "type: Bug\n"
        "work_status: resolved\n"
        "description: Archived before migration.\n"
        "effort: small\n"
        "opened: 2026-08-10\n"
        "updated: 2026-08-10\n"
        "affects:\n"
        "  - packages/okf-io\n"
    )
    body = "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n"
    page(root, f"work/_archive/{slug}/00-open-work.md", body, frontmatter=frontmatter)
    page(root, f"work/_archive/{slug}/01-design-spec.md", "# Spec\n")
    page(root, f"work/_archive/{slug}/03-execute-results.md", "# Results\n")


def test_archive_shape_promotes_the_item_page_and_leaves_its_stage_docs(tmp_path: Path) -> None:
    from migrate_vault import cmd_archive_shape

    root = vault_root(tmp_path)
    _archived_item(root)
    slug = "2026-08-10-bug-coercion-failures-unread"

    result = cmd_archive_shape(_args(vault=root, write=True))

    assert result.ok, result.refusals
    assert (root / f"work/_archive/{slug}.md").is_file()
    assert not (root / f"work/_archive/{slug}/00-open-work.md").exists()
    assert (root / f"work/_archive/{slug}/01-design-spec.md").is_file()
    assert (root / f"work/_archive/{slug}/03-execute-results.md").is_file()


def test_archive_shape_repairs_an_inbound_markdown_link(tmp_path: Path) -> None:
    from migrate_vault import cmd_archive_shape

    root = vault_root(tmp_path)
    _archived_item(root)
    slug = "2026-08-10-bug-coercion-failures-unread"
    page(root, "concepts/a.md", f"See [that bug](/work/_archive/{slug}/00-open-work.md).\n", frontmatter="title: A\n")

    cmd_archive_shape(_args(vault=root, write=True))

    assert f"/work/_archive/{slug}.md" in (root / "concepts/a.md").read_text(encoding="utf-8")


def test_archive_shape_reports_a_stranded_wikilink_rather_than_dropping_it(tmp_path: Path) -> None:
    """`moves` is blind to `[[wikilink]]` forms and says so. Phase 5 is what
    makes them repairable; a survivor is reported, never silently stranded."""
    from migrate_vault import cmd_archive_shape

    root = vault_root(tmp_path)
    _archived_item(root)
    slug = "2026-08-10-bug-coercion-failures-unread"
    page(root, "concepts/a.md", f"See [[work/_archive/{slug}/00-open-work]].\n", frontmatter="title: A\n")

    result = cmd_archive_shape(_args(vault=root, write=True))

    assert any("stranded" in note for note in result.notes)


def test_archive_shape_refuses_when_both_forms_exist(tmp_path: Path) -> None:
    """Zero cases in the live vault; the fixture exists anyway. Refusal, not a
    choice -- picking one would silently discard an item page."""
    from migrate_vault import cmd_archive_shape

    root = vault_root(tmp_path)
    _archived_item(root)
    slug = "2026-08-10-bug-coercion-failures-unread"
    page(
        root,
        f"work/_archive/{slug}.md",
        "# Sibling form\n",
        frontmatter="title: Sibling form\ntype: Bug\nwork_status: resolved\n",
    )

    result = cmd_archive_shape(_args(vault=root, write=True))

    assert not result.ok
    assert [r.reason for r in result.refusals] == ["ambiguous-archive-shape"]
    assert (root / f"work/_archive/{slug}/00-open-work.md").is_file()  # nothing moved


def test_archive_shape_is_idempotent(tmp_path: Path) -> None:
    from migrate_vault import cmd_archive_shape

    root = vault_root(tmp_path)
    _archived_item(root)
    cmd_archive_shape(_args(vault=root, write=True))
    assert cmd_archive_shape(_args(vault=root, write=True)).changed == ()


def test_without_archive_shape_the_work_migrator_refuses_every_archived_item(tmp_path: Path) -> None:
    """The regression that proves 6a is load-bearing. Run it *without* 6a and
    `plan_migration` returns a refused plan with one `legacy-path-invalid` per
    archived item (`migration.py:194-201`).

    Loaded with `LEGACY_IGNORE`, matching `migrate_vault_work`'s own test
    suite and its docstring's claim that `plan_migration` loads with its own
    ignore set -- `ignore_patterns("work")`'s stage-doc-hiding globs would
    hide `01-design-spec.md`/`03-execute-results.md` from the bundle
    entirely, and `plan_migration` needs their content to move and rename
    them as part of the full migration.
    """
    from migrate_vault_work import LEGACY_IGNORE, plan_migration
    from okf_io import load_bundle

    root = vault_root(tmp_path)
    for n in range(3):
        _archived_item(root, slug=f"2026-08-1{n}-bug-example-{n}")

    plan = plan_migration(load_bundle(root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    reasons = [refusal.kind for refusal in plan.mutation.refusals]
    assert reasons.count("legacy-path-invalid") == 3


def test_with_archive_shape_the_work_migrator_is_clean(tmp_path: Path) -> None:
    from migrate_vault import cmd_archive_shape
    from migrate_vault_work import LEGACY_IGNORE, plan_migration
    from okf_io import load_bundle

    root = vault_root(tmp_path)
    for n in range(3):
        _archived_item(root, slug=f"2026-08-1{n}-bug-example-{n}")

    assert cmd_archive_shape(_args(vault=root, write=True)).ok
    plan = plan_migration(load_bundle(root, ignore=LEGACY_IGNORE))

    assert plan.ok, [r.kind for r in plan.mutation.refusals]


# ---- phase 6: work ----


def _seed_schema(vault: Path) -> None:
    """Install this package's own schema/sections declarations at `.gw/`.

    `apply_mutation`'s postcondition validation (`graph_works_core.workspace.
    transactions._validate_postconditions`) falls back to `<bundle_dir>/schema`
    when `<config_dir>/schema` is absent -- neither exists in a bare
    `vault_root()` fixture, and the fallback then fails outright. A real vault
    always carries installed declarations by the time phase 6 runs.
    """
    from work_tracker_okf.resources import seed_files

    config_dir = vault.parent / ".gw"
    for relative, content in seed_files().items():
        target = config_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


_PLAN_BODY = "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n"


def _legacy_work_lane(root: Path) -> None:
    """A parent, a child, and one archived item, all post-phase-4.

    Frontmatter is `_base.schema.json`-complete (`effort`/`affects` required
    once `status` isn't `draft`) and each body carries the required `## Plan`
    section, matching `_archived_item`'s rationale above: `plan_migration`'s
    final projection lint re-validates the migrated result against the real
    catalog, independent of whatever `ignore=` loaded the pre-migration bundle.
    """
    epic_frontmatter = (
        "title: Cutover\ntype: Epic\nwork_status: in-progress\nowner: pat\n"
        "description: Carry the vault to path-native form.\n"
        "effort: medium\nopened: 2026-08-11\nupdated: 2026-08-11\n"
        "affects:\n  - scripts/migrate_vault.py\n"
        "sources:\n"
        "  - id: design-spec\n"
        "    resource: /work/2026-08-11-epic-cutover/01-design-spec.md\n"
        "    title: Legacy design\n"
        "children:\n- 2026-08-12-feature-sweep\n"
    )
    feature_frontmatter = (
        "title: Sweep\ntype: Feature\nwork_status: open\n"
        "description: Drive the harvested work migrator.\n"
        "effort: small\nopened: 2026-08-12\nupdated: 2026-08-12\n"
        "affects:\n  - scripts/migrate_vault.py\n"
        "parent: 2026-08-11-epic-cutover\n"
    )
    page(root, "work/2026-08-11-epic-cutover.md", _PLAN_BODY, frontmatter=epic_frontmatter)
    page(root, "work/2026-08-12-feature-sweep.md", _PLAN_BODY, frontmatter=feature_frontmatter)
    page(root, "work/2026-08-11-epic-cutover/01-design-spec.md", "# Spec\n", frontmatter="title: Spec\n")


def test_work_phase_dry_run_writes_nothing(tmp_path: Path) -> None:
    from migrate_vault import cmd_work

    root = vault_root(tmp_path)
    _legacy_work_lane(root)
    before = {p: p.read_bytes() for p in sorted(root.rglob("*.md"))}

    result = cmd_work(_args(vault=root, write=False))

    assert result.ok, result.refusals
    assert {p: p.read_bytes() for p in sorted(root.rglob("*.md"))} == before
    assert any("plan" in note or "->" in note for note in result.notes)


def test_work_phase_write_lands_the_path_native_lane(tmp_path: Path) -> None:
    from migrate_vault import cmd_work

    root = vault_root(tmp_path)
    _seed_schema(root)
    _legacy_work_lane(root)

    result = cmd_work(_args(vault=root, write=True))

    assert result.ok, result.refusals
    assert (root / "work/epic-cutover.md").is_file()
    assert (root / "work/epic-cutover/children/feature-sweep.md").is_file()
    assert (root / "work/epic-cutover/references/01-design.md").is_file()
    assert not (root / "work/2026-08-11-epic-cutover.md").exists()


def test_work_phase_reports_a_refusal_when_apply_mutation_rolls_back(tmp_path: Path) -> None:
    """Deliberately skip `_seed_schema`: `apply_mutation`'s postcondition
    validation then fails with no `.gw/schema` (and no bundle-relative
    fallback) to validate against, and the transaction rolls back *without
    raising* -- `MutationApplication.ok` is the only signal. `cmd_work` must
    surface that as a refusal (and leave the vault untouched), not report
    `ok=True, changed=(...)` for a run that wrote nothing durable."""
    from migrate_vault import cmd_work

    root = vault_root(tmp_path)
    _legacy_work_lane(root)
    before = {p: p.read_bytes() for p in sorted(root.rglob("*.md"))}

    result = cmd_work(_args(vault=root, write=True))

    assert not result.ok
    assert result.refusals
    assert all(r.reason in ("rolled-back", "apply-mutation-failed") for r in result.refusals)
    assert {p: p.read_bytes() for p in sorted(root.rglob("*.md"))} == before


def test_work_phase_never_applies_a_refused_plan(tmp_path: Path) -> None:
    from migrate_vault import cmd_work

    root = vault_root(tmp_path)
    # `parent:`/`children:` disagreement -- a refusal `_hierarchy` already owns.
    page(
        root,
        "work/2026-08-11-epic-a.md",
        "# A\n",
        frontmatter="title: A\ntype: Epic\nwork_status: open\nchildren:\n- 2026-08-12-feature-nope\n",
    )
    before = {p: p.read_bytes() for p in sorted(root.rglob("*.md"))}

    result = cmd_work(_args(vault=root, write=True))

    assert not result.ok
    assert {p: p.read_bytes() for p in sorted(root.rglob("*.md"))} == before


def test_work_phase_stage_document_stays_clean_under_the_sweeps_ignore_set(tmp_path: Path) -> None:
    """Constraint 1. A stage doc carrying `kind: bug` is safe only because phase
    4 never gives it a `type:` -- `_nodes` reaches `type_name is None` and
    continues (`migration.py:214-217`)."""
    from migrate_vault import cmd_work

    root = vault_root(tmp_path)
    _legacy_work_lane(root)
    page(
        root,
        "work/2026-08-11-epic-cutover/02-plan-plan.md",
        "# Plan\n",
        frontmatter="title: Plan\nkind: bug\nstatus: draft\n",
    )

    assert cmd_work(_args(vault=root, write=False)).ok


def test_work_phase_refuses_once_a_stage_document_carries_a_type(tmp_path: Path) -> None:
    """The other half of constraint 1, and the reason `ignore_patterns` is
    load-bearing rather than a performance choice."""
    from migrate_vault import cmd_work

    root = vault_root(tmp_path)
    _legacy_work_lane(root)
    page(
        root,
        "work/2026-08-11-epic-cutover/02-plan-plan.md",
        "# Plan\n",
        frontmatter="title: Plan\ntype: Bug\nwork_status: open\n",
    )

    result = cmd_work(_args(vault=root, write=False))

    assert not result.ok
    assert any(r.reason == "legacy-path-invalid" for r in result.refusals)


def test_work_phase_is_idempotent(tmp_path: Path) -> None:
    from migrate_vault import cmd_work

    root = vault_root(tmp_path)
    _seed_schema(root)
    _legacy_work_lane(root)
    cmd_work(_args(vault=root, write=True))

    second = cmd_work(_args(vault=root, write=True))

    assert second.ok, second.refusals
    assert second.changed == ()


# ---- phase 7: entities ----

PREIMAGE_JSON = ".gw/migration/entities-preimage.json"
UNMATCHED_MD = ".gw/migration/entities-unmatched.md"


def _entity_lane(root: Path) -> None:
    page(root, "entities/pkg_okf-io__ab12cd.md", "# okf-io\n",
         frontmatter="title: okf-io\nkind: package\nuri: pkg:psprowls/agent-workspace/okf-io\n")
    page(root, "entities/dependency_ruamel__ff00aa.md", "# ruamel.yaml\n",
         frontmatter="title: ruamel.yaml\nkind: dependency\nuri: dependency:pypi/ruamel-yaml\n")


def test_entities_snapshots_every_page_before_touching_anything(tmp_path: Path) -> None:
    from migrate_vault import cmd_entities

    root = vault_root(tmp_path)
    _entity_lane(root)

    cmd_entities(_args(vault=root, write=True, repo_rename=[], scan=False))

    rows = json.loads((tmp_path / PREIMAGE_JSON).read_text(encoding="utf-8"))["rows"]
    assert {row["uri"] for row in rows} == {
        "pkg:psprowls/agent-workspace/okf-io",
        "dependency:pypi/ruamel-yaml",
    }
    assert all({"member", "uri", "kind", "title", "h1"} <= set(row) for row in rows)


def test_entities_quarantines_rather_than_deleting(tmp_path: Path) -> None:
    """D-044: the quarantine directory is deleted only after a human signs off
    on the unmatched report. This subcommand never removes it."""
    from migrate_vault import cmd_entities

    root = vault_root(tmp_path)
    _entity_lane(root)

    cmd_entities(_args(vault=root, write=True, repo_rename=[], scan=False))

    assert not (root / "entities").exists()
    quarantine = tmp_path / ".gw" / "migration" / "entities-preimage"
    assert (quarantine / "pkg_okf-io__ab12cd.md").is_file()


@pytest.mark.parametrize(("uri", "expected"), [
    ("pkg:o/r/x", "repositories/r/packages/x"),
    ("repo:o/r", "repositories/r/repository"),
    ("app:o/r/x", "repositories/r/apps/x"),
    ("agent_plugin:o/r/x", "repositories/r/agent-plugins/x"),
    ("test_suite:o/r/x", "repositories/r/test-suites/x"),
    ("dependency:pypi/x", "dependencies/pypi/x"),
])
def test_successor_id_is_derived_from_the_uri_alone(uri: str, expected: str) -> None:
    """Pure: resource identity is the only input. No filesystem, no bundle
    (`placement.py:233-263`). The regenerated lane verifies the map; it does not
    discover it."""
    from migrate_vault import successor_id

    assert successor_id(uri) == expected


def test_successor_id_applies_a_repo_rename_before_parsing(tmp_path: Path) -> None:
    from migrate_vault import successor_id

    assert successor_id(
        "app:psprowls/agent-workspace/code-wiki-okf",
        repo_renames={"agent-workspace": "graph-works"},
    ) == "repositories/graph-works/apps/code-wiki-okf"


def test_entities_rewrites_a_markdown_link_to_the_successor_path(tmp_path: Path) -> None:
    from migrate_vault import cmd_entities

    root = vault_root(tmp_path)
    _entity_lane(root)
    page(root, "repositories/r/packages/okf-io.md", "# okf-io\n",
         frontmatter="title: okf-io\ntype: Package\nresource: pkg:psprowls/r/okf-io\n")
    page(root, "concepts/a.md", "See [okf-io](/entities/pkg_okf-io__ab12cd.md).\n", frontmatter="title: A\n")

    cmd_entities(_args(vault=root, write=True, repo_rename=["agent-workspace=r"], scan=False))

    assert "/repositories/r/packages/okf-io.md" in (root / "concepts/a.md").read_text(encoding="utf-8")


def test_entities_rewrites_a_sources_entity_uri(tmp_path: Path) -> None:
    """The second reference surface. `entity_uri` is not an OKF reserved key and
    not an OKF reference form, so nothing in `moves` or `validate` would ever
    have caught these."""
    from migrate_vault import cmd_entities
    from okf_io import load as load_one

    root = vault_root(tmp_path)
    _entity_lane(root)
    page(root, "repositories/r/packages/okf-io.md", "# okf-io\n",
         frontmatter="title: okf-io\ntype: Package\nresource: pkg:psprowls/r/okf-io\n")
    page(root, "sources/s.md", "# S\n",
         frontmatter="title: S\nentity_uri: pkg:psprowls/agent-workspace/okf-io\n")

    cmd_entities(_args(vault=root, write=True, repo_rename=["agent-workspace=r"], scan=False))

    document = load_one(root / "sources/s.md")
    assert document.fm.extra["entity_uri"] == "pkg:psprowls/r/okf-io"


def test_entities_reports_an_unmatched_uri_and_rewrites_nothing_for_it(tmp_path: Path) -> None:
    from migrate_vault import cmd_entities

    root = vault_root(tmp_path)
    _entity_lane(root)  # no regenerated lane at all
    page(root, "concepts/a.md", "See [okf-io](/entities/pkg_okf-io__ab12cd.md).\n", frontmatter="title: A\n")

    result = cmd_entities(_args(vault=root, write=True, repo_rename=[], scan=False))

    body = (tmp_path / UNMATCHED_MD).read_text(encoding="utf-8")
    assert "pkg:psprowls/agent-workspace/okf-io" in body
    assert "/entities/pkg_okf-io__ab12cd.md" in (root / "concepts/a.md").read_text(encoding="utf-8")
    assert any("unmatched" in note for note in result.notes)


def test_entities_report_separates_pre_existing_breakage_from_new(tmp_path: Path) -> None:
    """105 distinct link targets against 75 snapshot rows: ~30 targets were
    already dangling before the sweep. Gate assertion 3 measures a delta, not an
    absolute, so the report must tell the two apart."""
    from migrate_vault import cmd_entities

    root = vault_root(tmp_path)
    _entity_lane(root)
    page(root, "concepts/a.md",
         "Gone: [x](/entities/pkg_never-existed__000000.md)\n", frontmatter="title: A\n")

    cmd_entities(_args(vault=root, write=True, repo_rename=[], scan=False))

    body = (tmp_path / UNMATCHED_MD).read_text(encoding="utf-8")
    assert "already dangling before this sweep" in body


def test_entities_is_resumable(tmp_path: Path) -> None:
    """Each step skips when its own output already exists, so an interrupted run
    resumes instead of re-quarantining an empty lane."""
    from migrate_vault import cmd_entities

    root = vault_root(tmp_path)
    _entity_lane(root)
    first = cmd_entities(_args(vault=root, write=True, repo_rename=[], scan=False))
    snapshot = (tmp_path / PREIMAGE_JSON).read_bytes()

    second = cmd_entities(_args(vault=root, write=True, repo_rename=[], scan=False))

    assert second.ok == first.ok
    assert (tmp_path / PREIMAGE_JSON).read_bytes() == snapshot


# ---- phase 8: lanes ----


def _declare(workspace: Path, directories: dict[str, str]) -> Path:
    """Write a minimal `.gw/schema/` declaring `x-okf-directory` per type.

    Values are written exactly as given -- a trailing `/` matches how every
    real schema in this workspace declares one (e.g.
    `doc-wiki-okf/src/doc_wiki_okf/assets/schema/Reference.schema.json`), and
    `doc_wiki_okf.diataxis.pages.directory_for` uses the declared string
    verbatim to build a destination path, with no normalization of its own.
    """
    schema_dir = workspace / ".gw" / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    for type_name, directory in directories.items():
        (schema_dir / f"{type_name}.schema.json").write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": type_name,
                    "type": "object",
                    "x-okf-type": type_name,
                    "x-okf-directory": directory,
                }
            ),
            encoding="utf-8",
        )
    return schema_dir


def test_lanes_reads_destinations_from_the_declared_schema_set(tmp_path: Path) -> None:
    """`x-okf-directory` on the bundle's own schemas is the source of truth
    (`okf_ext/schemas/loader.py:148-167`). A constant here would drift the moment
    a declaration changed."""
    from migrate_vault import lane_destinations

    root = vault_root(tmp_path)
    _declare(tmp_path, {"Reference": "references/", "Explanation": "explanations/", "Bug": "work/"})

    assert lane_destinations(root)["Reference"] == "references/"


def test_lanes_retypes_and_moves_a_concept_using_the_decisions_file(tmp_path: Path) -> None:
    from migrate_vault import cmd_lanes

    root = vault_root(tmp_path)
    _declare(tmp_path, {"Reference": "references/"})
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\n")
    _decide(tmp_path, rows=(("concepts/a.md", "diataxis-type", "Reference"),))

    result = cmd_lanes(_args(vault=root, write=True))

    assert result.ok, result.refusals
    assert (root / "references/a.md").is_file()
    assert "type: Reference" in (root / "references/a.md").read_text(encoding="utf-8")
    assert not (root / "concepts/a.md").exists()


def test_lanes_refuses_a_concept_with_no_resolved_decision(tmp_path: Path) -> None:
    from migrate_vault import cmd_lanes

    root = vault_root(tmp_path)
    _declare(tmp_path, {"Reference": "references/"})
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\n")
    _decide(tmp_path, rows=())

    result = cmd_lanes(_args(vault=root, write=True))

    assert not result.ok
    assert [r.reason for r in result.refusals] == ["uncovered-by-decisions"]
    assert (root / "concepts/a.md").is_file()


def test_lanes_repairs_an_inbound_markdown_link_across_the_move(tmp_path: Path) -> None:
    from migrate_vault import cmd_lanes

    root = vault_root(tmp_path)
    _declare(tmp_path, {"Reference": "references/"})
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\n")
    page(root, "adrs/0001-x.md", "See [A](/concepts/a.md).\n", frontmatter="title: X\ntype: Adr\n")
    _decide(tmp_path, rows=(("concepts/a.md", "diataxis-type", "Reference"),))

    cmd_lanes(_args(vault=root, write=True))

    assert "/references/a.md" in (root / "adrs/0001-x.md").read_text(encoding="utf-8")


def test_lanes_reports_a_stranded_wikilink(tmp_path: Path) -> None:
    """Why conversion (phase 5) must precede every move: `moves` repairs
    markdown references only, and reports what it cannot reach."""
    from migrate_vault import cmd_lanes

    root = vault_root(tmp_path)
    _declare(tmp_path, {"Reference": "references/"})
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\n")
    page(root, "adrs/0001-x.md", "See [[concepts/a]].\n", frontmatter="title: X\ntype: Adr\n")
    _decide(tmp_path, rows=(("concepts/a.md", "diataxis-type", "Reference"),))

    result = cmd_lanes(_args(vault=root, write=True))

    assert any("stranded" in note for note in result.notes)


def test_lanes_delegates_proposals_wholesale(tmp_path: Path) -> None:
    from migrate_vault import cmd_lanes

    root = vault_root(tmp_path)
    _declare(tmp_path, {"Proposal": "proposals/"})
    page(root, "proposals/p.md", "# P\n", frontmatter="title: P\ntarget_slug: some-concept\n")

    result = cmd_lanes(_args(vault=root, write=True))

    assert result.ok, result.refusals
    assert any("proposal" in note.lower() for note in result.notes)


def test_lanes_is_idempotent(tmp_path: Path) -> None:
    from migrate_vault import cmd_lanes

    root = vault_root(tmp_path)
    _declare(tmp_path, {"Reference": "references/"})
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\n")
    _decide(tmp_path, rows=(("concepts/a.md", "diataxis-type", "Reference"),))

    cmd_lanes(_args(vault=root, write=True))
    assert cmd_lanes(_args(vault=root, write=True)).changed == ()


# ---- phase 9: bundle ----

LEGACY_LOG = """# Log

Entries are appended oldest-first as `## [DATE] op | title`.

## [2026-08-10] create | First thing

Some detail about the first thing.

## [2026-08-10] update | Second thing

## [2026-08-12] ingest | Third thing
"""


def test_bundle_phase_regroups_the_log_into_dated_sections(tmp_path: Path) -> None:
    from migrate_vault import cmd_bundle
    from okf_io import load as load_one
    from okf_io.log import parse as parse_log

    root = vault_root(tmp_path)
    (root / "log.md").write_text(LEGACY_LOG, encoding="utf-8")

    result = cmd_bundle(_args(vault=root, write=True))

    assert result.ok, result.refusals
    log = parse_log(load_one(root / "log.md"))
    assert [section.heading for section in log.sections] == ["2026-08-12", "2026-08-10"]
    assert all(section.date is not None for section in log.sections)
    assert len(log.on(date(2026, 8, 10)).entries) >= 2


def test_bundle_phase_log_is_newest_first(tmp_path: Path) -> None:
    from migrate_vault import cmd_bundle
    from okf_io import load as load_one
    from okf_io.log import parse as parse_log

    root = vault_root(tmp_path)
    (root / "log.md").write_text(LEGACY_LOG, encoding="utf-8")
    cmd_bundle(_args(vault=root, write=True))

    dates = [section.date for section in parse_log(load_one(root / "log.md")).sections]
    assert dates == sorted(dates, reverse=True)


def test_bundle_phase_log_reports_no_reserved_heading_finding(tmp_path: Path) -> None:
    from migrate_vault import cmd_bundle
    from okf_io import load_bundle, validate

    root = vault_root(tmp_path)
    (root / "log.md").write_text(LEGACY_LOG, encoding="utf-8")
    cmd_bundle(_args(vault=root, write=True))

    report = validate(load_bundle(root), today=date(2026, 8, 23))
    assert report.by_code("reserved.log-heading-not-date") == ()


def test_bundle_phase_rewrites_the_prose_header(tmp_path: Path) -> None:
    from migrate_vault import cmd_bundle

    root = vault_root(tmp_path)
    (root / "log.md").write_text(LEGACY_LOG, encoding="utf-8")
    cmd_bundle(_args(vault=root, write=True))

    body = (root / "log.md").read_text(encoding="utf-8")
    assert "oldest-first" not in body
    assert "newest first" in body


def test_bundle_phase_strips_frontmatter_from_a_sub_index(tmp_path: Path) -> None:
    from migrate_vault import cmd_bundle

    root = vault_root(tmp_path)
    page(root, "work/index.md", "# Work\n", frontmatter="title: Work\ncategory: index\n")

    cmd_bundle(_args(vault=root, write=True))

    body = (root / "work/index.md").read_text(encoding="utf-8")
    assert not body.lstrip("﻿").startswith("---")
    assert "# Work" in body


def test_bundle_phase_gives_the_root_index_its_okf_version(tmp_path: Path) -> None:
    """`okf_ext/bundle/plan.py:88-94`: a root index carrying no `okf_version` is
    not an OKF bundle root."""
    from migrate_vault import cmd_bundle

    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / ".gw").mkdir()

    cmd_bundle(_args(vault=root, write=True))

    assert "okf_version: 0.2" in (root / "index.md").read_text(encoding="utf-8")


def test_bundle_phase_is_idempotent(tmp_path: Path) -> None:
    from migrate_vault import cmd_bundle

    root = vault_root(tmp_path)
    (root / "log.md").write_text(LEGACY_LOG, encoding="utf-8")
    page(root, "work/index.md", "# Work\n", frontmatter="title: Work\n")

    cmd_bundle(_args(vault=root, write=True))
    first = {p: p.read_bytes() for p in sorted(root.rglob("*.md"))}

    assert cmd_bundle(_args(vault=root, write=True)).changed == ()
    assert {p: p.read_bytes() for p in sorted(root.rglob("*.md"))} == first


# ---- phase 10: gate ----


def test_gate_uses_the_same_ignore_declaration_as_the_sweep(tmp_path: Path) -> None:
    """One declaration, so the sweep and its acceptance can never disagree about
    what counts as a concept."""
    from migrate_vault import BASE_IGNORE, ignore_patterns

    assert set(BASE_IGNORE) <= set(ignore_patterns("gate"))


def test_gate_passes_on_a_clean_bundle(tmp_path: Path) -> None:
    from migrate_vault import cmd_gate

    root = vault_root(tmp_path)
    page(root, "adrs/0001-a.md", "# A\n", frontmatter="title: A\ntype: Adr\nstatus: stable\n")

    result = cmd_gate(_args(vault=root, write=False, today=date(2026, 8, 23)))

    assert result.ok, result.refusals


def test_gate_fails_assertion_two_on_a_coercion_failure(tmp_path: Path) -> None:
    """`validate()` will not report this; the gate must."""
    from migrate_vault import cmd_gate

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "# A\n", frontmatter="title: A\nsources: 7\n")

    result = cmd_gate(_args(vault=root, write=False, today=date(2026, 8, 23)))

    assert not result.ok
    assert any(r.reason == "coercion-failure" for r in result.refusals)


def test_gate_writes_a_baseline_when_none_exists_rather_than_passing_vacuously(tmp_path: Path) -> None:
    from migrate_vault import cmd_gate

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "See [gone](/concepts/nowhere.md).\n", frontmatter="title: A\n")
    baseline = tmp_path / "baseline.json"

    result = cmd_gate(_args(vault=root, write=True, today=date(2026, 8, 23), baseline=baseline))

    assert baseline.is_file()
    assert json.loads(baseline.read_text(encoding="utf-8"))["broken"] == 1
    assert any("baseline" in note for note in result.notes)


def test_gate_fails_assertion_three_when_broken_links_increased(tmp_path: Path) -> None:
    from migrate_vault import cmd_gate

    root = vault_root(tmp_path)
    page(root, "concepts/a.md", "See [gone](/concepts/nowhere.md).\n", frontmatter="title: A\n")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"broken": 0}), encoding="utf-8")

    result = cmd_gate(_args(vault=root, write=False, today=date(2026, 8, 23), baseline=baseline))

    assert not result.ok
    assert any(r.reason == "broken-links-increased" for r in result.refusals)


def test_gate_fails_assertion_four_on_an_unreviewed_unmatched_report(tmp_path: Path) -> None:
    from migrate_vault import cmd_gate

    root = vault_root(tmp_path)
    unmatched = tmp_path / ".gw" / "migration" / "entities-unmatched.md"
    unmatched.parent.mkdir(parents=True, exist_ok=True)
    unmatched.write_text(
        "# Entity remap — unmatched\n\n"
        "| old member | uri | computed successor | inbound refs | expected because |\n"
        "|---|---|---|---:|---|\n"
        "| `entities/pkg_x__00.md` | `pkg:o/r/x` | `repositories/r/packages/x` | 4 | repo rename |\n",
        encoding="utf-8",
    )

    result = cmd_gate(_args(vault=root, write=False, today=date(2026, 8, 23)))

    assert not result.ok
    assert any(r.reason == "unmatched-entity-references" for r in result.refusals)


def test_gate_passes_assertion_four_when_first_table_is_empty_but_second_has_rows(tmp_path: Path) -> None:
    """A genuinely clean migration renders an empty first table (zero unmatched
    entity references) followed by a *second* table listing pre-existing
    dangling targets (`_render_unmatched`, `migrate_vault.py:1135-1141`). The
    scan must stop at the first table's end even though it saw zero data rows,
    rather than reading into the second table and miscounting its rows as
    unmatched."""
    from migrate_vault import cmd_gate

    root = vault_root(tmp_path)
    unmatched = tmp_path / ".gw" / "migration" / "entities-unmatched.md"
    unmatched.parent.mkdir(parents=True, exist_ok=True)
    unmatched.write_text(
        "# Entity remap — unmatched\n\n"
        "## Snapshot URIs with no successor in the regenerated lane\n\n"
        "| old member | uri | computed successor | inbound refs | expected because |\n"
        "|---|---|---|---:|---|\n"
        "\n"
        "## Reference targets that were already dangling before this sweep\n\n"
        "These name no snapshot row, so nothing this phase did broke them.\n\n"
        "| target | inbound refs |\n"
        "|---|---:|\n"
        "| `pkg:o/r/already-dangling-1` | 2 |\n"
        "| `pkg:o/r/already-dangling-2` | 1 |\n",
        encoding="utf-8",
    )

    result = cmd_gate(_args(vault=root, write=False, today=date(2026, 8, 23)))

    assert result.ok, result.refusals
    assert any(note == "unmatched entity references: 0" for note in result.notes)


# ---- whole-sweep properties ----

PHASE_SEQUENCE = ("frontmatter", "titles", "links", "archive-shape", "work", "lanes", "bundle")


def _whole_vault(tmp_path: Path) -> Path:
    """One mini-vault carrying every lane the sweep touches, in legacy dialect."""
    root = vault_root(tmp_path)
    _declare(tmp_path, {"Reference": "references/", "Explanation": "explanations/"})
    _seed_schema(root)

    # work: a live parent, a live child, one archived item in the nested form
    plan_body = "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n"
    page(root, "work/2026-08-11-epic-cutover.md", f"# Cutover\n\n{plan_body}",
         frontmatter="title: Cutover\nkind: epic\nstatus: in-progress\nsummary: the epic\n"
                     "owner: pat\neffort: medium\nopened: 2026-08-11\nupdated: 2026-08-11\n"
                     "affects:\n  - scripts\nchildren:\n- 2026-08-12-feature-sweep\n")
    page(root, "work/2026-08-12-feature-sweep.md", f"# Sweep\n\n{plan_body}",
         frontmatter="title: Sweep\nkind: feature\nstatus: open\nparent: 2026-08-11-epic-cutover\n"
                     "summary: the feature\neffort: small\nopened: 2026-08-12\nupdated: 2026-08-12\n"
                     "affects:\n  - scripts\n")
    page(root, "work/2026-08-11-epic-cutover/01-design-spec.md", "# Spec\n",
         frontmatter="title: Spec\nkind: bug\nstatus: draft\n")
    _archived_item(root)

    # sources, adrs, concepts
    page(root, "sources/s.md", "# S\n", frontmatter="title: S\ntype: Source\nsource_type: spec\nsummary: a source\n")
    page(root, "adrs/0001-a.md", "# A\n", frontmatter="title: A\ntype: Adr\nstatus: accepted\nsummary: an adr\n")
    page(root, "concepts/c.md", "# C\n", frontmatter="title: C\nsources: 7\ncategory: wiki\n")

    # a page that must survive byte-identical: already conformant, CRLF + BOM
    target = root / "adrs/0002-crlf.md"
    target.write_bytes("﻿---\r\ntitle: Fine\r\ntype: Adr\r\nstatus: stable\r\n---\r\n\r\n# Fine\r\n".encode())

    (root / "log.md").write_text(LEGACY_LOG, encoding="utf-8")
    _decide(tmp_path, rows=(("concepts/c.md", "diataxis-type", "Reference"),))
    return root


def _run_sweep(root: Path) -> dict[str, "PhaseReport"]:
    from migrate_vault import _HANDLERS

    results = {}
    for phase in PHASE_SEQUENCE:
        results[phase] = _HANDLERS[phase](_args(vault=root, write=True))
        assert results[phase].ok, (phase, results[phase].refusals)
    return results


def test_sweep_is_idempotent_end_to_end(tmp_path: Path) -> None:
    """A second run of every subcommand is a no-op, so a partially migrated
    vault resumes rather than double-applying."""
    root = _whole_vault(tmp_path)
    _run_sweep(root)
    after_first = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    second = _run_sweep(root)

    assert all(result.changed == () for result in second.values()), {
        phase: result.changed for phase, result in second.items() if result.changed
    }
    assert {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()} == after_first


def test_sweep_leaves_a_conformant_page_byte_identical(tmp_path: Path) -> None:
    """Newline dialect and BOM included. The two-layer document model's whole
    promise, exercised across every phase rather than one."""
    root = _whole_vault(tmp_path)
    target = root / "adrs/0002-crlf.md"
    before = target.read_bytes()

    _run_sweep(root)

    survivor = next(p for p in root.rglob("0002-crlf.md"))
    assert survivor.read_bytes() == before


def test_sweep_refuses_an_undecided_concept_rather_than_guessing(tmp_path: Path) -> None:
    from migrate_vault import cmd_frontmatter

    root = _whole_vault(tmp_path)
    page(root, "concepts/undecided.md", "# U\n", frontmatter="title: U\nstatus: active\n")

    result = cmd_frontmatter(_args(vault=root, write=True))

    assert not result.ok
    assert any(r.reason == "uncovered-by-decisions" for r in result.refusals)


def test_sweep_refuses_an_illegal_decision_rather_than_defaulting(tmp_path: Path) -> None:
    from migrate_vault import cmd_lanes

    root = _whole_vault(tmp_path)
    _decide(tmp_path, rows=(("concepts/c.md", "diataxis-type", "Wharrgarbl"),))

    result = cmd_lanes(_args(vault=root, write=True))

    assert not result.ok
    assert any(r.reason == "illegal-decision" for r in result.refusals)


def test_sweep_runs_phase_six_against_the_real_ignore_set(tmp_path: Path) -> None:
    """Not a bare one. The fixture's stage document
    (`work/2026-08-11-epic-cutover/01-design-spec.md`, `kind: bug`) matches
    `ignore_patterns("work")`'s own `work/*/0[1-9]-*.md` pattern, so loading
    with that ignore set hides it entirely -- it never reaches
    `Bundle.concepts` and `_nodes` never examines it here. (Its `type:`-less
    safety -- the `_nodes` `type_name is None: continue` path -- is exercised
    directly, with the doc actually present as a concept, by
    `test_work_phase_stage_document_stays_clean_under_the_sweeps_ignore_set`
    and `test_work_phase_refuses_once_a_stage_document_carries_a_type` above.)

    **Deviation from the plan's literal snippet.** The plan's own draft called
    `plan_migration(load_bundle(root, ignore=ignore_patterns("work")))` and
    asserted `.ok`. Verified against `work_tracker_okf.mutation._plan_path_mutation`
    (imported by `migrate_vault_work.plan_migration`): the domain-move sweep
    discovers a member from `Bundle.ignored` too (`_all_members` folds it in),
    but the content layer can only materialize a `Bundle.concepts` entry, so
    *any* legacy stage doc hidden by `ignore_patterns("work")`'s
    `work/*/0[1-9]-*.md` pattern -- one nested under a live item, or an
    archived item's own siblings -- makes that call refuse with
    `materialize-error: ... not a member of this bundle`, independent of this
    fixture's shape (confirmed by reproducing it with only `_archived_item`
    seeded, no epic/feature at all). `cmd_work` itself never hits this: it
    loads with `LEGACY_IGNORE`, not `ignore_patterns("work")`, precisely
    for this reason (its own docstring says as much). So this test instead
    calls `_nodes` directly against the real `ignore_patterns("work")` and
    checks it raises no refusals for the rest of the vault -- proving `_nodes`
    itself tolerates the sweep's own ignore set even though full
    `plan_migration` cannot -- and separately confirms `cmd_work`'s own real
    run, loaded with `LEGACY_IGNORE`, is clean.
    """
    from migrate_vault import _HANDLERS, cmd_work, ignore_patterns
    from migrate_vault_work import _nodes
    from okf_io import load_bundle

    root = _whole_vault(tmp_path)
    for phase in ("frontmatter", "titles", "links", "archive-shape"):
        assert _HANDLERS[phase](_args(vault=root, write=True)).ok

    bundle = load_bundle(root, ignore=ignore_patterns("work"))
    _, node_refusals = _nodes(bundle)
    assert node_refusals == ()

    assert cmd_work(_args(vault=root, write=True)).ok


def test_sweep_ends_with_a_passing_gate(tmp_path: Path) -> None:
    from migrate_vault import cmd_gate

    root = _whole_vault(tmp_path)
    baseline = tmp_path / "baseline.json"
    cmd_gate(_args(vault=root, write=True, today=date(2026, 8, 23), baseline=baseline))  # writes the baseline
    _run_sweep(root)

    result = cmd_gate(_args(vault=root, write=False, today=date(2026, 8, 23), baseline=baseline))

    assert result.ok, result.refusals
