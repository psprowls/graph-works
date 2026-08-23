from datetime import date

import pytest
from okf_io import parse
from work_tracker_okf.paths import MANAGED_ARTIFACTS, ArtifactRef, artifact_ref, references_dir
from work_tracker_okf.sources import upsert

_PATH = "work/release-r1/children/epic-migration/children/feature-filing-writer"

_BARE = """\
---
type: Feature
title: The filing writer
description: A page with no sources yet.
tags:
  - fixture
status: draft
work_status: open
opened: 2026-03-02
updated: 2026-03-02
---

## Options considered

One.

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |

## Notes / log
"""

_STAMPED = """\
---
type: Feature
title: The filing writer
description: A page with one source already.
tags:
  - fixture
status: stable
sources:
  - id: design
    resource: /work/release-r1/children/epic-migration/children/feature-filing-writer/references/01-design.md
    title: Design spec
    last_modified: '2026-03-02'
    reviewed_by: human:pat
work_status: accepted
opened: 2026-03-02
updated: 2026-03-04
---

## Options considered

One.

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
| Ship it | Tests pass | It is a fixture |

## Notes / log
"""


def _around_sources(text: str) -> tuple[str, str]:
    """Everything before the `sources:` key, and the body after the frontmatter.

    The two halves `upsert` promises not to touch. Splitting on the literal keys
    rather than diffing line numbers keeps the assertion readable when the
    `sources:` block itself changes length."""
    head, marker, rest = text.partition("\nsources:\n")
    assert marker, "expected a `sources:` key in the rendered document"
    _, _, tail = rest.partition("\n---\n")
    return head, tail


def test_the_first_upsert_writes_the_entry() -> None:
    document = parse(_BARE)
    ref = artifact_ref(_PATH, MANAGED_ARTIFACTS["design"])
    assert upsert(document, ref, title="Design spec") is True
    entries = document.fm_data()["sources"]
    assert entries == [
        {
            "id": "design",
            "resource": f"/{_PATH}/references/01-design.md",
            "title": "Design spec",
        }
    ]


def test_last_modified_is_written_as_an_iso_string() -> None:
    """P-2: `fm_data()` projects dates to ISO strings, so writing a `date` would
    make the second pass compare `str` against `date` and never converge."""
    document = parse(_BARE)
    upsert(document, artifact_ref(_PATH, MANAGED_ARTIFACTS["plan"]), title="Plan", last_modified=date(2026, 3, 4))
    assert document.fm_data()["sources"][0]["last_modified"] == "2026-03-04"
    assert "last_modified: '2026-03-04'" in document.serialize()


def test_a_second_identical_upsert_returns_false_and_changes_no_byte() -> None:
    document = parse(_BARE)
    ref = artifact_ref(_PATH, MANAGED_ARTIFACTS["design"])
    assert upsert(document, ref, title="Design spec", last_modified=date(2026, 3, 2)) is True
    once = document.serialize()
    assert upsert(document, ref, title="Design spec", last_modified=date(2026, 3, 2)) is False
    assert document.serialize() == once


def test_an_update_in_place_preserves_an_unknown_key() -> None:
    """C2-G: the merge is on raw data and never re-projects through
    `okf_io.Source`, which would silently drop any key the dataclass does not
    model."""
    document = parse(_STAMPED)
    ref = artifact_ref(_PATH, MANAGED_ARTIFACTS["design"])
    assert upsert(document, ref, title="Design spec, revised") is True
    entry = document.fm_data()["sources"][0]
    assert entry["title"] == "Design spec, revised"
    assert entry["reviewed_by"] == "human:pat"
    assert entry["last_modified"] == "2026-03-02"


def test_a_new_entry_appends_and_leaves_the_existing_order_alone() -> None:
    document = parse(_STAMPED)
    assert upsert(document, artifact_ref(_PATH, MANAGED_ARTIFACTS["plan"]), title="Plan") is True
    assert [entry["id"] for entry in document.fm_data()["sources"]] == ["design", "plan"]


def test_a_ref_without_a_source_id_raises() -> None:
    document = parse(_BARE)
    with pytest.raises(ValueError, match="no source_id"):
        upsert(document, references_dir(_PATH), title="References")
    with pytest.raises(ValueError, match="no source_id"):
        upsert(document, ArtifactRef(rel="work/anything.md"), title="Anything")


def test_the_write_touches_no_line_outside_the_sources_span() -> None:
    """C2-G's one stated cost: `Document.set` replaces the whole `sources` key, so
    the diff is minimal at *block* granularity rather than at key granularity.
    Everything outside the block stays byte-identical."""
    document = parse(_STAMPED)
    before_head, before_tail = _around_sources(document.serialize())
    upsert(document, artifact_ref(_PATH, MANAGED_ARTIFACTS["plan"]), title="Plan")
    after_head, after_tail = _around_sources(document.serialize())
    assert after_head == before_head
    assert after_tail == before_tail


def test_a_non_mapping_entry_in_the_list_survives() -> None:
    """Nothing on the content path raises: a malformed entry is left where it is
    rather than dropped, so a page is never quietly edited into shape."""
    document = parse(_BARE)
    document.set("sources", ["not-a-mapping"])
    assert upsert(document, artifact_ref(_PATH, MANAGED_ARTIFACTS["plan"]), title="Plan") is True
    assert document.fm_data()["sources"][0] == "not-a-mapping"
    assert document.fm_data()["sources"][1]["id"] == "plan"
