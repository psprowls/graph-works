"""`session_bodies`: verified artifact bodies for titles, served once per queue pass."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from _display_fixture import disable_index
from graph_works_core.read_session import open as read_session_open
from graph_works_core.work.artifact_bodies import session_bodies
from okf_ext.readindex import PageContent
from okf_io import Unreadable
from test_run_next import _layout, _spec, _write
from work_tracker_okf.compose import stamp_for
from work_tracker_okf.paths import ArtifactRef
from work_tracker_okf.pipeline import PACKAGED_DEFINITION

DESIGN = "work/feature-design"
REF = ArtifactRef(rel=f"{DESIGN}/references/01-design.md", source_id="design")


class _Session:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def content(self, member_id):
        self.calls.append(member_id)
        return self.answer


def _page(body: str) -> PageContent:
    return PageContent(id=REF.rel, frontmatter={}, body=body, parse_error=None, coercion_failures=frozenset())


def test_a_page_serves_its_body_once() -> None:
    session = _Session(_page("# Served\n"))
    body_of = session_bodies(session)
    assert body_of(REF) == "# Served\n"
    assert body_of(REF) == "# Served\n"
    assert session.calls == [REF.rel]


@pytest.mark.parametrize("answer", [None, Unreadable("could not be read")])
def test_not_a_member_and_unreadable_defer_to_the_file_once(answer) -> None:
    session = _Session(answer)
    body_of = session_bodies(session)
    assert body_of(REF) is None
    assert body_of(REF) is None
    assert session.calls == [REF.rel]


def test_each_supplier_has_its_own_cache_keyed_by_raw_member_id() -> None:
    session = _Session(_page("# First\n"))
    first = session_bodies(session)
    assert first(REF) == "# First\n"
    session.answer = _page("# Second\n")
    alias = ArtifactRef(rel=REF.rel, source_id="custom-design")
    assert first(alias) == "# First\n"
    assert session_bodies(session)(alias) == "# Second\n"
    assert session.calls == [REF.rel, REF.rel]


@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_a_real_session_serves_reference_artifacts_outside_the_work_lens(tmp_path: Path, backend: str) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    _spec(layout, DESIGN)
    if backend == "bundle":
        disable_index(layout)
    with read_session_open.open_read_session(layout) as session:
        assert session.backend == backend
        assert session.work_snapshot()
        row = session.member(REF.rel, ignore=("*/references/*",))
        assert row is not None and row.kind == "ignored"
        assert session_bodies(session)(REF) == "# Design\n"


@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_a_non_markdown_member_defers_to_the_authoritative_stamp_read(tmp_path: Path, backend: str) -> None:
    layout = _layout(tmp_path)
    _write(layout, DESIGN)
    artifact = _spec(layout, DESIGN).with_suffix(".txt")
    artifact.write_text("---\ntype: Spec\n# YAML comment\n---\n# Text artifact\n", encoding="utf-8", newline="\n")
    definition = replace(
        PACKAGED_DEFINITION,
        artifacts={
            **PACKAGED_DEFINITION.artifacts,
            "design": replace(PACKAGED_DEFINITION.artifacts["design"], file=artifact.name),
        },
    )
    ref = ArtifactRef(rel=f"{DESIGN}/references/{artifact.name}", source_id="design")
    if backend == "bundle":
        disable_index(layout)
    with read_session_open.open_read_session(layout) as session:
        body_of = session_bodies(session)
        (item,) = session.work_snapshot()
        assert body_of(ref) is None
        assert stamp_for(layout.bundle_dir, item, "design", definition=definition, body_of=body_of) == (
            ref,
            "Text artifact",
        )
