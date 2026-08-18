"""One file in, a fact sheet out — and the legacy dict out of `as_data()`.

The parity cases are ported from `wiki-io`'s `test_ingest_source_prep.py`. That
suite reached a real sqlite code graph to produce an entity match; here the
matcher is a stub, because the graph is a seam now (design spec §5.2).
"""

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from doc_wiki_okf.ingest.document import PREVIEW_CHARS, DocumentBrief, plan_document_brief
from doc_wiki_okf.ingest.layout import IngestLayout
from doc_wiki_okf.ingest.seams import NO_ENTITY, EntityMatch

DAY = date(2026, 8, 12)
GATE = {"scanned_at": "2026-08-11", "stale": True}


def _gate(repo: Path, /, *, workspace: Path) -> Mapping[str, Any]:
    return GATE


def _matcher(repo: Path, source: Path, title: str, /) -> EntityMatch:
    return EntityMatch(uri="pkg:o/r/graph-io", entity_filename="pkg_graph-io")


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    return tmp_path, wiki


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_spec_carries_the_supplied_type(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source = _write(workspace / "material" / "auth.md", "# Auth Spec\n\nBody text.")

    brief = plan_document_brief(source, wiki=wiki, repo=repo, workspace_root=workspace, today=DAY, source_kind="spec")

    assert brief.source_kind == "spec"
    assert brief.title == "Auth Spec"
    assert brief.slug == "auth-spec"
    assert brief.suggested_summary_path == "sources/2026-08-auth-spec.md"
    assert brief.merge_mode is False
    assert brief.word_count == 4


def test_the_kind_has_no_effect_on_any_other_field(tmp_path):
    """K-G: `doc` used to be the one value with an effect -- it set
    `in_repo_doc`, which nothing read. Two briefs differing only in their kind
    now differ only in their kind, which is what makes the value inert rather
    than merely carried."""
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "packages" / "graph-io" / "store.py", "# Graph IO Store\n\nBody text.")

    def brief_for(kind: str):
        return plan_document_brief(
            Path("packages/graph-io/store.py"),
            wiki=wiki,
            repo=workspace,
            workspace_root=workspace,
            today=DAY,
            source_kind=kind,
        )

    as_doc = brief_for("doc")
    as_spec = brief_for("spec")

    assert as_doc.source_path == source
    assert as_doc.source_kind == "doc"
    assert as_spec.source_kind == "spec"
    assert as_doc.as_data() | {"source_kind": "spec"} == as_spec.as_data()


def test_material_outside_the_workspace_briefs_the_same(tmp_path, tmp_path_factory):
    """S-H: material is ingested from outside the workspace now, and nothing in
    the brief depends on where it sits."""
    workspace, wiki = _workspace(tmp_path)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    source = _write(elsewhere / "auth.md", "# Auth Spec\n\nBody text.")

    brief = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="spec"
    )

    assert brief.source_kind == "spec"
    assert brief.suggested_summary_path == "sources/2026-08-auth-spec.md"


def test_a_title_falls_back_to_the_stem(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "some-long-read.txt", "no heading here")

    brief = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="article"
    )

    assert brief.title == "Some Long Read"
    assert brief.slug == "some-long-read"


def test_no_seams_means_two_nulls(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "a.md", "# A\n")

    brief = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="spec"
    )

    assert brief.state_gate is None
    assert brief.entity_match == NO_ENTITY


def test_both_seams_ride_through(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "a.md", "# A\n")

    brief = plan_document_brief(
        source,
        wiki=wiki,
        repo=workspace,
        workspace_root=workspace,
        today=DAY,
        source_kind="spec",
        state_gate=_gate,
        match_entity=_matcher,
    )

    assert brief.state_gate == GATE
    assert brief.entity_match == EntityMatch(uri="pkg:o/r/graph-io", entity_filename="pkg_graph-io")


def test_today_decides_the_source_page(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "a.md", "# A\n")

    january = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=date(2026, 1, 5), source_kind="spec"
    )
    august = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="spec"
    )

    assert january.suggested_summary_path == "sources/2026-01-a.md"
    assert august.suggested_summary_path == "sources/2026-08-a.md"


def test_merge_mode_is_true_when_the_source_page_exists(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "a.md", "# A\n")
    _write(wiki / "sources" / "2026-08-a.md", "# A\n")

    brief = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="spec"
    )

    assert brief.merge_mode is True


def test_a_long_document_is_truncated_and_says_so(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    body = "x " * PREVIEW_CHARS
    source = _write(workspace / "material" / "long.md", f"# Long\n\n{body}")

    brief = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="spec"
    )

    assert brief.preview.endswith("[TRUNCATED]")
    assert len(brief.preview) == PREVIEW_CHARS + len("\n[TRUNCATED]")


def test_a_short_document_is_not_truncated(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "short.md", "# Short\n\nbody")

    brief = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="spec"
    )

    assert "[TRUNCATED]" not in brief.preview


def test_the_layout_decides_the_source_page_template(tmp_path):
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "a.md", "# A\n")
    layout = IngestLayout(source_page_template="pages/{slug}-{month}.md")

    brief = plan_document_brief(
        source,
        wiki=wiki,
        repo=workspace,
        workspace_root=workspace,
        today=DAY,
        source_kind="spec",
        layout=layout,
    )

    assert brief.source_kind == "spec"
    assert brief.suggested_summary_path == "pages/a-2026-08.md"


def test_text_is_the_full_extract_not_the_preview(tmp_path):
    """C3: `brief.text` is the whole extract, not `preview`'s truncated head --
    the reasoner needs the full document, and `preview` is capped at
    `PREVIEW_CHARS`."""
    workspace, wiki = _workspace(tmp_path)
    body = "x " * PREVIEW_CHARS
    source = _write(workspace / "material" / "long.md", f"# Long\n\n{body}")

    brief = plan_document_brief(
        source, wiki=wiki, repo=workspace, workspace_root=workspace, today=DAY, source_kind="spec"
    )

    assert brief.text == f"# Long\n\n{body}"
    assert len(brief.text) > len(brief.preview)
    assert brief.preview.startswith(brief.text[:PREVIEW_CHARS])


def test_as_data_is_the_legacy_dict(tmp_path):
    """Shape and key parity with the legacy dict, with two documented
    departures: `word_count` uses corrected computation, and `source_type` is
    now `source_kind` (K-A). `in_repo_doc` is gone entirely (K-G)."""
    workspace, wiki = _workspace(tmp_path)
    source = _write(workspace / "material" / "auth.md", "# Auth Spec\n\nBody text.")

    data = plan_document_brief(
        source,
        wiki=wiki,
        repo=workspace,
        workspace_root=workspace,
        today=DAY,
        source_kind="spec",
        state_gate=_gate,
        match_entity=_matcher,
    ).as_data()

    assert data == {
        "source_path": str(source),
        "title": "Auth Spec",
        "source_kind": "spec",
        "slug": "auth-spec",
        "preview": "# Auth Spec\n\nBody text.",
        "word_count": 4,
        "suggested_summary_path": "sources/2026-08-auth-spec.md",
        "merge_mode": False,
        "entity_match": {"uri": "pkg:o/r/graph-io", "entity_filename": "pkg_graph-io"},
        "state_gate": {"scanned_at": "2026-08-11", "stale": True},
    }
    assert json.loads(json.dumps(data)) == data


def test_the_brief_is_frozen_and_carries_no_discriminator():
    fields = {f.name for f in DocumentBrief.__dataclass_fields__.values()}
    assert not fields & {"is_folder", "is_batch", "is_skill"}
