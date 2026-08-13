"""One plan, two create writes: the source page and its reference copy."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from doc_wiki_okf.cli import IGNORE
from doc_wiki_okf.sources import plan_ingest
from okf_ext.proposals import apply
from okf_io import load_bundle
from source_helpers import AT, BY, TODAY, build_bundle, material, schema_set, section_set

PAGE = "sources/2026-08-auth-spec.md"
COPY = "sources/references/2026-08-auth-spec.md"


def _plan(root: Path, path: Path, text: str, *, bundle=None, **overrides):
    kwargs = {
        "title": "Auth Spec",
        "description": "The authentication specification.",
        "source_type": "spec",
        "origin": "https://example.invalid/auth-spec",
        "by": BY,
        "at": AT,
        "today": TODAY,
    }
    kwargs.update(overrides)
    bundle = build_bundle(root) if bundle is None else bundle
    return plan_ingest(bundle, schema_set(), section_set(), path, text=text, **kwargs), bundle


def test_a_plan_carries_exactly_two_creates_page_first(tmp_path: Path) -> None:
    path, text = material(tmp_path)
    plan, _ = _plan(tmp_path / "bundle", path, text)

    assert plan.ok
    assert [(write.member, write.mode) for write in plan.writes] == [(PAGE, "create"), (COPY, "create")]
    assert plan.target == PAGE
    assert plan.proposal is None


def test_the_page_carries_the_type_the_required_keys_and_the_origin(tmp_path: Path) -> None:
    path, text = material(tmp_path)
    plan, _ = _plan(tmp_path / "bundle", path, text)

    rendered = plan.writes[0].text
    assert "type: Source" in rendered
    assert "title: Auth Spec" in rendered
    assert "description: The authentication specification." in rendered
    assert "source_type: spec" in rendered
    assert f"source_path: {COPY}" in rendered
    assert "origin: https://example.invalid/auth-spec" in rendered
    assert "ingested: '2026-08-12'" in rendered or "ingested: 2026-08-12" in rendered
    assert "generated:" in rendered


def test_the_page_body_is_the_declared_skeleton(tmp_path: Path) -> None:
    path, text = material(tmp_path)
    plan, _ = _plan(tmp_path / "bundle", path, text)

    rendered = plan.writes[0].text
    for heading in ("## TL;DR", "## Key claims", "## Where it's cited in this wiki"):
        assert heading in rendered


def test_a_supplied_body_replaces_the_skeleton(tmp_path: Path) -> None:
    path, text = material(tmp_path)
    plan, _ = _plan(tmp_path / "bundle", path, text, body="## TL;DR\n\nWritten by the caller.\n")

    assert "Written by the caller." in plan.writes[0].text
    assert "## Key claims" not in plan.writes[0].text


def test_the_copy_is_the_material_byte_for_byte(tmp_path: Path) -> None:
    """No normalization, no frontmatter, no trailing-newline fixups."""
    odd = "no trailing newline\r\n\tand a tab"
    path, text = material(tmp_path, text=odd)
    plan, _ = _plan(tmp_path / "bundle", path, text)

    assert plan.writes[1].text == odd


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("notes.md", "sources/references/2026-08-auth-spec.md"),
        ("notes.html", "sources/references/2026-08-auth-spec.html"),
        ("NOTES.TXT", "sources/references/2026-08-auth-spec.txt"),
        ("notes", "sources/references/2026-08-auth-spec.txt"),
    ],
)
def test_the_copy_extension_comes_from_the_material(tmp_path: Path, name: str, expected: str) -> None:
    path, text = material(tmp_path, name=name)
    plan, _ = _plan(tmp_path / "bundle", path, text)

    assert plan.writes[1].member == expected


def test_the_page_and_the_copy_share_a_stem(tmp_path: Path) -> None:
    """A human reading `sources/<stem>.md` finds its material at
    `sources/references/<stem>.*` without consulting frontmatter."""
    path, text = material(tmp_path, name="notes.html")
    plan, _ = _plan(tmp_path / "bundle", path, text)

    page_stem = Path(plan.writes[0].member).stem
    copy_stem = Path(plan.writes[1].member).stem
    assert page_stem == copy_stem == "2026-08-auth-spec"


def test_a_re_ingest_refuses_target_exists(tmp_path: Path) -> None:
    """S-E: `plan_create` creates, it never overwrites. A human who genuinely
    wants to re-ingest deletes the page and re-runs."""
    path, text = material(tmp_path)
    bundle = build_bundle(tmp_path / "bundle", {PAGE: "---\ntype: Source\n---\n\n## TL;DR\n"})
    plan, _ = _plan(tmp_path / "bundle", path, text, bundle=bundle, description="d", origin="o")

    assert not plan.ok
    assert plan.writes == ()
    assert [(r.kind, r.path) for r in plan.refusals] == [("target-exists", PAGE)]


def test_an_occupied_copy_refuses_even_when_the_page_is_free(tmp_path: Path) -> None:
    path, text = material(tmp_path)
    bundle = build_bundle(tmp_path / "bundle", {COPY: "already here"})
    plan, _ = _plan(tmp_path / "bundle", path, text, bundle=bundle, description="d", origin="o")

    assert not plan.ok
    assert plan.writes == ()
    assert [(r.kind, r.path) for r in plan.refusals] == [("target-exists", COPY)]


def test_an_occupied_copy_is_seen_through_the_ignore_list(tmp_path: Path) -> None:
    """`has_member` counts ignored members: `ignore=` declares 'this is not a
    concept', not 'this is not there'. Component 3 ignores `sources/references/*`,
    so the occupancy check depends on exactly that distinction."""
    bundle = build_bundle(tmp_path / "bundle", {COPY: "already here"})
    assert bundle.has_member(COPY)
    assert "sources/references/2026-08-auth-spec" not in bundle.concepts


def test_a_title_that_slugs_to_nothing_lands_at_untitled(tmp_path: Path) -> None:
    """`slugify` never returns empty -- it falls back to `untitled` -- so a blank
    title produces a valid target rather than a refusal. Asserted so nobody
    plans a `target-escapes-bundle` path that cannot be reached from here."""
    path, text = material(tmp_path)
    plan, _ = _plan(tmp_path / "bundle", path, text, title="   ")

    assert plan.ok
    assert plan.writes[0].member == "sources/2026-08-untitled.md"
    assert plan.writes[1].member == "sources/references/2026-08-untitled.md"


def test_an_undeclared_source_type_raises(tmp_path: Path) -> None:
    """Caller configuration, not bundle content -- matching `new_page_text`."""
    path, text = material(tmp_path)
    bundle = build_bundle(tmp_path / "bundle")
    stripped = replace(schema_set(), schemas={k: v for k, v in schema_set().schemas.items() if k != "Source"})
    with pytest.raises(KeyError, match="Source"):
        plan_ingest(
            bundle,
            stripped,
            section_set(),
            path,
            text=text,
            title="Auth Spec",
            description="d",
            source_type="spec",
            origin="o",
            by=BY,
            at=AT,
            today=TODAY,
        )


def test_apply_writes_both_members_and_the_copy_is_not_a_concept(tmp_path: Path) -> None:
    """Acceptance 3 and 5, as one round trip."""
    root = tmp_path / "bundle"
    path, text = material(tmp_path)
    plan, bundle = _plan(root, path, text)

    result = apply(bundle, plan)

    assert result.ok
    assert list(result.written) == [PAGE, COPY]
    assert (root / PAGE).is_file()
    assert (root / COPY).read_text(encoding="utf-8") == text

    reloaded = load_bundle(root, ignore=IGNORE)
    assert reloaded.has_member(COPY)
    assert "sources/references/2026-08-auth-spec" not in reloaded.concepts
    assert reloaded.concepts["sources/2026-08-auth-spec"].fm.type == "Source"


def test_the_pair_is_atomic_when_the_copy_cannot_be_staged(tmp_path: Path) -> None:
    """`write_all`'s probe-and-staging regime is all-or-nothing: if either
    target's parent cannot be made, no live file is touched. The claim under
    Component 2, tested rather than asserted."""
    root = tmp_path / "bundle"
    path, text = material(tmp_path)
    plan, bundle = _plan(root, path, text)

    # A *file* where the copy's parent directory must go, so `mkdir` fails.
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "references").write_text("not a directory", encoding="utf-8")

    result = apply(bundle, plan)

    assert not result.ok
    assert not (root / PAGE).exists()
    assert [failure.kind for failure in result.failed] == ["mkdir-error"]


def test_the_two_step_flow_yields_a_backlink(tmp_path: Path) -> None:
    """Acceptance 6. `sources[].resource` already accepts a member path and
    `build_link_graph()` already derives backlinks from it -- the two-step flow
    needs no new machinery, and this is what proves it."""
    from doc_wiki_okf.proposals.filing import plan_file
    from doc_wiki_okf.proposals.lanes import lane_set
    from okf_io import build_link_graph

    root = tmp_path / "bundle"
    path, text = material(tmp_path)
    plan, bundle = _plan(root, path, text)
    assert apply(bundle, plan).ok

    reloaded = load_bundle(root, ignore=IGNORE)
    filing = plan_file(
        reloaded,
        lane_set(schema_set()),
        lane="explanation",
        title="Why Auth Works This Way",
        description="Derived from the auth spec.",
        source={"id": "src-auth", "resource": PAGE},
        by=BY,
        at=AT,
    )
    assert apply(reloaded, filing).ok

    final = load_bundle(root, ignore=IGNORE)
    graph = build_link_graph(final)
    # `ReviewRenderer` renders each source's `resource` as a root-absolute
    # markdown link in the filed page's "Origins" section. `build_link_graph()`
    # resolves that link and records it in `LinkGraph.backlinks`, keyed by the
    # target concept id (the resolved path with `.md` stripped). `plan_propose`
    # stages the proposal under `proposals/`, not at the eventual lane target,
    # so that is the id that appears -- `filing.proposal` is exactly it.
    assert filing.proposal == "proposals/explanations-why-auth-works-this-way.md"
    assert "proposals/explanations-why-auth-works-this-way" in graph.backlinks["sources/2026-08-auth-spec"]
