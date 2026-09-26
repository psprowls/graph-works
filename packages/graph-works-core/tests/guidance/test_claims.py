"""The claims index: derived, cached, never stale when read, never raising on content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_core.guidance import claims as c
from okf_io import load_bundle

ADR = """\
---
type: Adr
title: Walk
status: stable
decisions:
  - id: D1
    claim: The bundle walk excludes dot-entries only at the root.
    about: [pkg:o/r/okf-io]
    constrains: [packages/okf-io/src/okf_io/bundle.py]
  - id: D2
    claim: Plans live under references.
    about: [repo:o/r]
    phase: [plan]
---

# Walk
"""

EXPLANATION = """\
---
type: Explanation
title: Old
status: superseded
superseded_by: /adrs/walk.md
claims:
  - id: C1
    claim: Old claim.
    about: [pkg:o/r/okf-io]
---

# Old
"""


def _write(root: Path, concept_id: str, text: str) -> None:
    target = root / f"{concept_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def bundle_root(tmp_path: Path) -> Path:
    root = tmp_path / "okf"
    _write(root, "adrs/walk", ADR)
    _write(root, "explanations/old", EXPLANATION)
    _write(root, "reference/plain", "---\ntype: Reference\ntitle: Plain\n---\n\n# Plain\n")
    return root


def test_first_refresh_builds_rows_with_every_field(bundle_root: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    result = c.refresh_claims(load_bundle(bundle_root), cache)
    assert (result.rebuilt, result.reason, result.pages, result.extracted, result.pruned) == (
        True,
        "no-manifest",
        2,
        2,
        0,
    )
    rows = c.read_claims(load_bundle(bundle_root), cache)
    by_id = {(row.page, row.id): row for row in rows}
    d1 = by_id[("adrs/walk", "D1")]
    assert d1.kind == "decision"
    assert d1.about == ("pkg:o/r/okf-io",)
    assert d1.constrains == ("packages/okf-io/src/okf_io/bundle.py",)
    assert d1.phases == c.PIPELINE_PHASES == ("design", "plan", "execute", "finish")
    assert d1.phase_source == "derived"
    assert d1.page_status == "stable"
    assert (d1.superseded, d1.superseded_by) == (False, None)
    assert d1.tokens > 0
    d2 = by_id[("adrs/walk", "D2")]
    assert (d2.phases, d2.phase_source, d2.constrains) == (("plan",), "authored", ())
    c1 = by_id[("explanations/old", "C1")]
    assert (c1.kind, c1.superseded, c1.superseded_by) == ("claim", True, "/adrs/walk.md")
    assert (cache / "claims" / "manifest.json").is_file()
    assert (cache / "claims" / "rows.json").is_file()


def test_second_refresh_is_idempotent_and_writes_nothing(bundle_root: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    before = {p: p.stat().st_mtime_ns for p in (cache / "claims").iterdir()}
    again = c.refresh_claims(load_bundle(bundle_root), cache)
    assert (again.rebuilt, again.reason, again.extracted) == (False, None, 0)
    assert {p: p.stat().st_mtime_ns for p in (cache / "claims").iterdir()} == before


def test_editing_one_page_reextracts_only_that_page(bundle_root: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    _write(bundle_root, "adrs/walk", ADR.replace("Plans live", "Plans always live"))
    result = c.refresh_claims(load_bundle(bundle_root), cache)
    assert (result.reason, result.extracted, result.pruned) == ("corpus-changed", 1, 0)


def test_deleting_a_page_prunes_it_and_a_new_claims_key_joins(bundle_root: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    (bundle_root / "explanations" / "old.md").unlink()
    _write(
        bundle_root,
        "reference/plain",
        "---\ntype: Reference\ntitle: Plain\nclaims:\n  - id: C1\n    claim: New.\n    about: [repo:o/r]\n---\n",
    )
    result = c.refresh_claims(load_bundle(bundle_root), cache)
    assert (result.pages, result.extracted, result.pruned) == (2, 1, 1)
    pages = {row.page for row in c.read_claims(load_bundle(bundle_root), cache)}
    assert pages == {"adrs/walk", "reference/plain"}


def test_claims_about_excludes_superseded_by_default(bundle_root: Path, tmp_path: Path) -> None:
    rows = c.read_claims(load_bundle(bundle_root), tmp_path / "cache")
    assert [(r.page, r.id) for r in c.claims_about(rows, "pkg:o/r/okf-io")] == [("adrs/walk", "D1")]
    assert [(r.page, r.id) for r in c.claims_about(rows, "pkg:o/r/okf-io", include_superseded=True)] == [
        ("adrs/walk", "D1"),
        ("explanations/old", "C1"),
    ]
    assert c.claims_about(rows, "pkg:o/r/unknown") == ()


@pytest.mark.parametrize(
    ("entries", "reason"),
    [
        ("  - just a string\n", "not-a-mapping"),
        ("  - claim: No id.\n    about: [repo:o/r]\n", "missing-id"),
        ("  - id: C9\n    about: [repo:o/r]\n", "missing-claim"),
        ("  - id: C9\n    claim: X.\n    about: repo:o/r\n", "about-not-a-list"),
        ("  - id: C9\n    claim: X.\n    about: [1]\n", "about-not-a-list"),
        ("  - id: C9\n    claim: X.\n    about: [repo:o/r]\n    constrains: a.py\n", "constrains-not-a-list"),
        ("  - id: C9\n    claim: X.\n    about: [repo:o/r]\n    phase: plan\n", "phase-not-a-list"),
    ],
)
def test_malformed_entry_is_skipped_and_reported_siblings_indexed(tmp_path: Path, entries: str, reason: str) -> None:
    root = tmp_path / "okf"
    good = "  - id: C1\n    claim: Good.\n    about: [repo:o/r]\n"
    _write(root, "e/p", f"---\ntype: Explanation\ntitle: P\nclaims:\n{good}{entries}---\n")
    result = c.refresh_claims(load_bundle(root), tmp_path / "cache")
    assert result.skipped == (c.SkippedEntry("e/p", "claims", 1, reason),)
    assert [r.id for r in c.read_claims(load_bundle(root), tmp_path / "cache")] == ["C1"]


def test_non_list_claims_key_and_unparseable_page_never_raise(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(root, "e/mapping", "---\ntype: Explanation\ntitle: M\nclaims: {id: C1}\n---\n")
    _write(root, "e/broken", "---\ntype: Explanation\nclaims: [\n---\n")
    result = c.refresh_claims(load_bundle(root), tmp_path / "cache")
    assert c.SkippedEntry("e/mapping", "claims", -1, "not-a-list") in result.skipped
    assert c.read_claims(load_bundle(root), tmp_path / "cache") == ()


def test_schema_version_mismatch_rebuilds_every_page(bundle_root: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    manifest = cache / "claims" / "manifest.json"
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["schema_version"] = c.CLAIMS_SCHEMA_VERSION + 1
    manifest.write_text(json.dumps(raw), encoding="utf-8", newline="\n")
    result = c.refresh_claims(load_bundle(bundle_root), cache)
    assert (result.reason, result.extracted) == ("schema-changed", 2)


@pytest.mark.parametrize("text", ["not json", "[]", '{"schema_version": true, "corpus_fingerprint": "x"}', "{}"])
def test_malformed_manifest_reads_as_no_manifest(bundle_root: Path, tmp_path: Path, text: str) -> None:
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    (cache / "claims" / "manifest.json").write_text(text, encoding="utf-8", newline="\n")
    assert c.refresh_claims(load_bundle(bundle_root), cache).reason == "no-manifest"


@pytest.mark.parametrize("damage", ["delete", "garbage", "wrong-shape"])
def test_missing_or_corrupt_rows_under_a_valid_manifest_rebuilds(
    bundle_root: Path, tmp_path: Path, damage: str
) -> None:
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    rows = cache / "claims" / "rows.json"
    if damage == "delete":
        rows.unlink()
    elif damage == "garbage":
        rows.write_text("{", encoding="utf-8", newline="\n")
    else:
        rows.write_text('{"pages": []}', encoding="utf-8", newline="\n")
    result = c.refresh_claims(load_bundle(bundle_root), cache)
    assert (result.rebuilt, result.reason, result.extracted) == (True, "rows-unreadable", 2)
    assert len(c.read_claims(load_bundle(bundle_root), cache)) == 3


def test_rows_emptied_under_a_valid_manifest_is_not_read_as_fresh(bundle_root: Path, tmp_path: Path) -> None:
    """A shape-valid but wrong `rows.json` (e.g. hand-edited to `{"pages": {}}`)

    passes `_read_rows` but its per-page hashes disagree with the corpus, so
    it must not be served as a fresh, empty index.
    """
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    (cache / "claims" / "rows.json").write_text('{"pages": {}}', encoding="utf-8", newline="\n")
    result = c.refresh_claims(load_bundle(bundle_root), cache)
    assert (result.rebuilt, result.reason, result.extracted) == (True, "rows-unreadable", 2)
    assert len(c.read_claims(load_bundle(bundle_root), cache)) == 3


def test_interrupted_build_rows_disagreeing_with_the_old_manifest_is_detected(
    bundle_root: Path, tmp_path: Path
) -> None:
    """Simulates a crash between the rows write and the manifest write.

    `_build` writes `rows.json` for the *new* corpus, then the manifest last.
    A crash in between leaves rows.json describing the new corpus under an
    old manifest that still describes the old one. Reproduced here by
    building for an edited corpus, restoring the old manifest bytes over the
    new one, then reverting the corpus back to what the old manifest actually
    describes: the stale rows must still be detected and rebuilt, not served.
    """
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    manifest_path = cache / "claims" / "manifest.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")

    _write(bundle_root, "adrs/walk", ADR.replace("Plans live", "Plans always live"))
    c.build_claims(load_bundle(bundle_root), cache)  # writes rows.json + manifest for the edited corpus
    manifest_path.write_text(original_manifest, encoding="utf-8", newline="\n")  # simulate the crash

    _write(bundle_root, "adrs/walk", ADR)  # revert to what the old manifest describes

    result = c.refresh_claims(load_bundle(bundle_root), cache)
    assert result.reason == "rows-unreadable"
    rows = c.read_claims(load_bundle(bundle_root), cache)
    by_id = {(row.page, row.id): row for row in rows}
    assert by_id[("adrs/walk", "D2")].claim == "Plans live under references."


def test_empty_corpus_writes_an_empty_fresh_index(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(root, "reference/plain", "---\ntype: Reference\ntitle: Plain\n---\n")
    cache = tmp_path / "cache"
    first = c.refresh_claims(load_bundle(root), cache)
    assert (first.rebuilt, first.pages) == (True, 0)
    assert c.refresh_claims(load_bundle(root), cache).reason is None
    assert c.read_claims(load_bundle(root), cache) == ()


def test_build_claims_is_unconditional(bundle_root: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    c.refresh_claims(load_bundle(bundle_root), cache)
    forced = c.build_claims(load_bundle(bundle_root), cache)
    assert (forced.rebuilt, forced.reason, forced.extracted) == (True, "forced", 0)


def test_row_json_round_trips_and_rejects_bad_shapes() -> None:
    row = c.ClaimRow("p", "D1", "decision", "x", ("repo:o/r",), (), ("plan",), "authored", None, False, None, 1)
    assert c.ClaimRow.from_json(row.to_json()) == row
    assert c.ClaimRow.from_json({"page": "p"}) is None
    assert c.ClaimRow.from_json([]) is None


# --- R1: `about` inheritance (curated-page claims contract) ----------------


def test_entry_with_no_about_inherits_page_about(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(
        root,
        "e/inherit",
        "---\ntype: Explanation\ntitle: Inherit\nabout: [repo:o/r, pkg:o/r/okf-io]\nclaims:\n"
        "  - id: C1\n    claim: Inherits the page about.\n---\n",
    )
    result = c.refresh_claims(load_bundle(root), tmp_path / "cache")
    assert result.skipped == ()
    rows = c.read_claims(load_bundle(root), tmp_path / "cache")
    assert len(rows) == 1
    assert rows[0].about == ("repo:o/r", "pkg:o/r/okf-io")


def test_entry_with_no_about_and_no_page_about_is_skipped_as_missing_about(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(root, "e/noabout", "---\ntype: Explanation\ntitle: NoAbout\nclaims:\n  - id: C1\n    claim: X.\n---\n")
    result = c.refresh_claims(load_bundle(root), tmp_path / "cache")
    assert result.skipped == (c.SkippedEntry("e/noabout", "claims", 0, "missing-about"),)
    assert c.read_claims(load_bundle(root), tmp_path / "cache") == ()


def test_entry_with_no_about_and_page_about_not_a_list_is_about_not_a_list(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(
        root,
        "e/badpageabout",
        "---\ntype: Explanation\ntitle: BadPageAbout\nabout: repo:o/r\nclaims:\n  - id: C1\n    claim: X.\n---\n",
    )
    result = c.refresh_claims(load_bundle(root), tmp_path / "cache")
    assert result.skipped == (c.SkippedEntry("e/badpageabout", "claims", 0, "about-not-a-list"),)
    assert c.read_claims(load_bundle(root), tmp_path / "cache") == ()


# --- R2: `superseded_by` as a list ------------------------------------------


def test_superseded_by_list_uses_first_non_empty_element(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(
        root,
        "adrs/multi",
        "---\ntype: Adr\ntitle: Multi\nsuperseded_by: [/adrs/a.md, /adrs/b.md]\ndecisions:\n"
        "  - id: D1\n    claim: X.\n    about: [repo:o/r]\n---\n",
    )
    rows = c.read_claims(load_bundle(root), tmp_path / "cache")
    assert len(rows) == 1
    assert (rows[0].superseded, rows[0].superseded_by) == (True, "/adrs/a.md")


def test_superseded_by_null_is_not_superseded_unless_status_says_so(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(
        root,
        "adrs/nullsup",
        "---\ntype: Adr\ntitle: Null\nsuperseded_by: null\ndecisions:\n"
        "  - id: D1\n    claim: X.\n    about: [repo:o/r]\n---\n",
    )
    rows = c.read_claims(load_bundle(root), tmp_path / "cache")
    assert len(rows) == 1
    assert (rows[0].superseded, rows[0].superseded_by) == (False, None)


def test_rows_unreadable_between_refresh_and_read_still_serves_rows(
    bundle_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rows.json that goes bad after the refresh is rebuilt, and its rows served -- never `()`."""
    cache = tmp_path / "cache"
    bundle = load_bundle(bundle_root)
    expected = c.read_claims(bundle, cache)
    assert expected
    real_refresh = c.refresh_claims

    def refresh_then_corrupt(b: object, cache_dir: Path) -> c.ClaimsRefresh:
        result = real_refresh(b, cache_dir)  # type: ignore[arg-type]
        (cache_dir / c.CLAIMS_SUBDIR / c.ROWS_NAME).write_text("{not json", encoding="utf-8", newline="\n")
        return result

    monkeypatch.setattr(c, "refresh_claims", refresh_then_corrupt)
    assert c.read_claims(bundle, cache) == expected
    monkeypatch.undo()
    # The rebuild left a readable, fresh index behind.
    assert c.refresh_claims(bundle, cache).reason is None


def test_writes_are_atomic_replacements_leaving_no_temp_files(bundle_root: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    c.build_claims(load_bundle(bundle_root), cache)
    c.build_claims(load_bundle(bundle_root), cache)
    assert sorted(p.name for p in (cache / c.CLAIMS_SUBDIR).iterdir()) == [c.MANIFEST_NAME, c.ROWS_NAME]


def test_failed_write_leaves_the_previous_file_and_no_temp(
    bundle_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    c.build_claims(load_bundle(bundle_root), cache)
    rows_path = cache / c.CLAIMS_SUBDIR / c.ROWS_NAME
    before = rows_path.read_bytes()

    def boom(self: Path, target: object) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        c.build_claims(load_bundle(bundle_root), cache)
    assert rows_path.read_bytes() == before
    assert sorted(p.name for p in rows_path.parent.iterdir()) == [c.MANIFEST_NAME, c.ROWS_NAME]
