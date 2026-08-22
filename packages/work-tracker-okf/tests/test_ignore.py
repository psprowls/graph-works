from pathlib import Path

from okf_io import load_bundle
from work_tracker_okf.items import ARCHIVE_IGNORE, IGNORE, load_items

#: Written out literally, not recomputed from the tier-2 defaults (C1-D). The
#: composition is this package's exported contract, so it must break loudly if
#: either default moves -- recomputing it here would make the test agree with
#: whatever okf-ext happens to say today.
_EXPECTED = (
    "*/references/*",
    "*/.DS_Store",
    "schema/*",
    "*/schema/*",
    "sections/*",
    "*/sections/*",
)

_ARTIFACTS = (
    "work/feature-beta/references/01-design-spec.md",
    "work/feature-beta/references/02-plan-plan.md",
    "work/feature-beta/references/03-plan-transcript.txt",
)


def test_ignore_is_the_composed_recipe_written_out() -> None:
    assert IGNORE == _EXPECTED


def test_references_pages_are_concepts_without_the_recipe(minimal_root: Path) -> None:
    """The other half of the property below: without `*/references/*` these
    pages *are* concepts, so the assertion that they are not is about the
    pattern rather than about the fixture."""
    bundle = load_bundle(minimal_root)
    assert "work/feature-beta/references/01-design-spec" in bundle.concepts


def test_the_recipe_drops_every_artifact_from_concepts(minimal_root: Path) -> None:
    bundle = load_bundle(minimal_root, ignore=IGNORE)
    assert not [cid for cid in bundle.concepts if "/references/" in cid]


def test_every_artifact_is_still_a_member(minimal_root: Path) -> None:
    """`ignore=` declares "this is not a concept", not "this is not there" --
    so a link into `references/` is a working link, not a broken one. The
    non-markdown member is the case `Bundle.assets` exists for."""
    bundle = load_bundle(minimal_root, ignore=IGNORE)
    for artifact in _ARTIFACTS:
        assert bundle.has_member(artifact), artifact


def test_a_nested_ds_store_is_ignored_by_the_read_lens_but_still_a_member(tmp_path: Path) -> None:
    """The nested half of ADR-0028's root-scoped dot exclusion.

    Built here rather than dropped into the fixture vault: that vault is a
    byte-exact contract, and a file macOS writes on its own is exactly what a
    committed fixture must not carry. The root `.DS_Store` is deliberately
    present too -- okf-io's walk drops that one, so the assertion below is
    about the nested pattern rather than about both.
    """
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "item.md").write_text("---\ntype: Bug\ntitle: T\n---\n\n# T\n", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"\x00root")
    (tmp_path / "work" / ".DS_Store").write_bytes(b"\x00nested")

    bundle = load_bundle(tmp_path, ignore=IGNORE)
    assert "work/.DS_Store" not in bundle.assets
    assert "work/.DS_Store" in bundle.ignored
    # Ignored still means present, which is the whole distinction `ignore=` draws.
    assert bundle.has_member("work/.DS_Store")

    # The move lens must still see it, or the emptied directory never prunes.
    moving = load_bundle(tmp_path, ignore=ARCHIVE_IGNORE)
    assert "work/.DS_Store" in moving.assets


def test_the_recipe_leaves_the_item_pages_alone(minimal_root: Path) -> None:
    bundle = load_bundle(minimal_root, ignore=IGNORE)
    assert {item.slug for item in load_items(bundle)} == {
        "broken-eta",
        "bug-gamma",
        "bug-theta",
        "epic-alpha",
        "feature-beta",
        "spike-zeta",
        "tech-debt-delta",
        "test-gap-epsilon",
    }


def test_the_recipe_keeps_this_packages_own_declarations_out_of_concepts(tmp_path: Path) -> None:
    """The round trip that matters in a real vault: install the fourteen files
    and load the result. None of them may become a concept."""
    from datetime import date

    from work_tracker_okf.init import install_bundle

    root = tmp_path / "bundle"
    install_bundle(root, today=date(2026, 1, 1), dry_run=False)
    bundle = load_bundle(root, ignore=IGNORE)
    assert dict(bundle.concepts) == {}
    assert bundle.has_member("schema/_base.schema.json")
    assert bundle.has_member("sections/_fragments.work_tracker.yaml")


#: Written out literally for the same reason `_EXPECTED` is: this is the
#: package's second exported recipe, and recomputing it from the tier-2
#: defaults would make the test agree with whatever okf-ext says today.
_EXPECTED_ARCHIVE = (
    "schema/*",
    "*/schema/*",
    "sections/*",
    "*/sections/*",
)


def test_archive_ignore_is_the_composed_recipe_written_out() -> None:
    assert ARCHIVE_IGNORE == _EXPECTED_ARCHIVE


def test_the_two_recipes_differ_by_exactly_the_move_blind_patterns() -> None:
    """C4-A. Two constants that drift apart silently is the failure mode, and
    a literal assertion on the delta is what prevents it.

    Both entries in the delta are there for one reason: `okf_ext.moves` never
    reads `bundle.ignored`, so anything hidden from the archive's own lens is
    left behind by the move. `*/references/*` would half-archive an item;
    `*/.DS_Store` would strand a file that keeps `apply` from pruning the
    emptied directory.
    """
    assert tuple(pattern for pattern in IGNORE if pattern not in ARCHIVE_IGNORE) == (
        "*/references/*",
        "*/.DS_Store",
    )
    assert tuple(pattern for pattern in ARCHIVE_IGNORE if pattern not in IGNORE) == ()


def test_the_archive_recipe_makes_artifacts_visible_to_moves(minimal_root: Path) -> None:
    """The property the whole archive path rests on: `moves` builds its mapping
    from concepts, assets, indexes and logs -- never from `bundle.ignored`."""
    bundle = load_bundle(minimal_root, ignore=ARCHIVE_IGNORE)
    assert "work/feature-beta/references/01-design-spec" in bundle.concepts
    assert "work/feature-beta/references/03-plan-transcript.txt" in bundle.assets


def test_load_items_is_lens_independent(minimal_root: Path) -> None:
    """Spec test 2. `_identify` returns `None` for any concept id whose
    remainder under `work/` contains a `/`, so the wider lens adds concepts
    that project to nothing -- which is what lets one walk serve both
    eligibility and the move plan."""
    assert load_items(load_bundle(minimal_root, ignore=IGNORE)) == load_items(
        load_bundle(minimal_root, ignore=ARCHIVE_IGNORE)
    )


def test_the_ledger_is_ignored_by_the_reader_recipe(conformant_root: Path) -> None:
    """§3.1: the ledger lives inside `references/`, so `*/references/*` already
    covers it. No new pattern, and no `IGNORE` / `ARCHIVE_IGNORE` delta churn."""
    from work_tracker_okf.paths import decisions_ledger

    ref = decisions_ledger("2026-03-01-epic-conformant-vault")
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    assert ref.rel in bundle.ignored
    assert bundle.has_member(ref.rel)


def test_the_ledger_is_visible_to_the_archive_recipe(conformant_root: Path) -> None:
    """The other half: `ARCHIVE_IGNORE` is `IGNORE` minus `*/references/*`, so
    `okf_ext.moves` — which never reads `bundle.ignored` — sees the ledger and
    the archive carries it along."""
    from work_tracker_okf.paths import decisions_ledger

    ref = decisions_ledger("2026-03-01-epic-conformant-vault")
    bundle = load_bundle(conformant_root, ignore=ARCHIVE_IGNORE)
    assert ref.rel.removesuffix(".md") in bundle.concepts


def test_adding_the_ledger_grew_no_ignore_pattern() -> None:
    """The claim §3.1 buys: `_EXPECTED` above is unchanged by this port."""
    from work_tracker_okf.paths import LEDGER_FILENAME

    stem = LEDGER_FILENAME.removesuffix(".md")
    assert stem not in " ".join(IGNORE)
    assert stem not in " ".join(ARCHIVE_IGNORE)
