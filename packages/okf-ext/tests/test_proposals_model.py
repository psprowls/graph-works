"""The capability's frozen values: shapes, defaults, and the closed vocabularies."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from okf_ext.proposals.model import (
    PAGE_STATUSES,
    DecisionPlan,
    PagePlan,
    PageRender,
    Proposal,
    ProposalPlan,
    Refusal,
    Write,
    source_ids,
)


def test_the_page_status_vocabulary_is_closed_and_ordered():
    """Ledger order, not alphabetical: `proposed` -> decided -> `created` is
    the transition sequence, and reading it in that order is what makes the
    two legal transitions obvious."""
    assert PAGE_STATUSES == ("proposed", "approved", "rejected", "created")


@pytest.mark.parametrize("cls", [Refusal, Proposal, PageRender, Write, ProposalPlan, DecisionPlan, PagePlan])
def test_every_value_is_frozen_and_slotted(cls):
    """Matching the core and every sibling capability. A mutable plan is a
    preview a caller can invalidate between inspecting it and applying it."""
    assert cls.__dataclass_params__.frozen
    assert "__slots__" in cls.__dict__


def test_every_value_is_immutable():
    """Frozen dataclasses forbid attribute mutation on constructed instances."""
    refusal = Refusal(path="p", kind="target-exists", detail="d")
    with pytest.raises(AttributeError):
        refusal.path = "other"  # type: ignore[misc]

    proposal = Proposal(
        member="m",
        concept_id="c",
        target="t",
        title="ti",
        description="d",
        page_status="proposed",
        raw_page_status="proposed",
        sources=(),
        verified=(),
    )
    with pytest.raises(AttributeError):
        proposal.target = "other"  # type: ignore[misc]

    page_render = PageRender(type="Page", body="b")
    with pytest.raises(AttributeError):
        page_render.type = "other"  # type: ignore[misc]

    write = Write(member="m", mode="create", text="t")
    with pytest.raises(AttributeError):
        write.mode = "update"  # type: ignore[misc]

    proposal_plan = ProposalPlan(root=Path("/tmp"), target="t", proposal="p", writes=(), refusals=())
    with pytest.raises(AttributeError):
        proposal_plan.target = "other"  # type: ignore[misc]

    decision_plan = DecisionPlan(root=Path("/tmp"), proposal="p", decision="approved", writes=(), refusals=())
    with pytest.raises(AttributeError):
        decision_plan.decision = "rejected"  # type: ignore[misc]

    page_plan = PagePlan(root=Path("/tmp"), target="t", mode="create", proposal=None, writes=(), refusals=())
    with pytest.raises(AttributeError):
        page_plan.mode = "update"  # type: ignore[misc]


def test_a_create_write_carries_whole_text_and_an_update_carries_keys():
    create = Write(member="pages/new.md", mode="create", text="---\n---\nbody\n")
    assert create.text == "---\n---\nbody\n"
    assert create.frontmatter == {}
    assert create.body is None
    assert create.digest is None

    update = Write(
        member="proposals/x.md",
        mode="update",
        frontmatter={"page_status": "approved"},
        body="new body\n",
        digest="abc",
    )
    assert update.text == ""
    assert dict(update.frontmatter) == {"page_status": "approved"}


def test_mapping_defaults_are_read_only():
    """A plain `dict` default would make a directly constructed value writable
    while a built one is not, so the class would honour its own contract only
    half the time -- the reason `okf_io.models` uses `MappingProxyType`."""
    with pytest.raises(TypeError):
        Write(member="x.md", mode="create").frontmatter["k"] = "v"  # type: ignore[index]
    with pytest.raises(TypeError):
        PageRender(type="Page", body="b").frontmatter["k"] = "v"  # type: ignore[index]


@pytest.mark.parametrize("cls", [ProposalPlan, DecisionPlan, PagePlan])
def test_a_plan_is_ok_until_it_carries_a_refusal(cls):
    kwargs = {"root": Path("/tmp/bundle"), "writes": (), "refusals": ()}
    if cls is ProposalPlan:
        kwargs |= {"target": "pages/a.md", "proposal": "proposals/a.md"}
    if cls is DecisionPlan:
        kwargs |= {"proposal": "proposals/a.md", "decision": "approved"}
    if cls is PagePlan:
        kwargs |= {"target": "pages/a.md", "mode": "create", "proposal": None}

    empty = cls(**kwargs)
    assert empty.ok
    assert empty.is_empty

    refused = dataclasses.replace(
        empty, refusals=(Refusal(path="pages/a.md", kind="target-exists", detail="already a member"),)
    )
    assert not refused.ok


def test_a_proposal_is_plain_data_all_the_way_down():
    """`sources` and `verified` come from `Document.fm_data()`, okf-io's
    `json.dumps`-able projection, so a proposal survives a round trip through
    JSON with no encoder -- the same promise `fm_data` itself makes."""
    proposal = Proposal(
        member="proposals/a.md",
        concept_id="proposals/a",
        target="pages/a.md",
        title="A",
        description="why",
        page_status="proposed",
        raw_page_status="proposed",
        sources=({"id": "src-a", "resource": "/sources/a.md", "title": "A"},),
        verified=(),
    )
    assert json.loads(json.dumps(dataclasses.asdict(proposal)))["target"] == "pages/a.md"
    assert proposal.malformed is None


def test_source_ids_returns_ids_in_order_blanks_included():
    """Extract every `id` from sources in order, blanks as empty strings."""
    sources = ({"id": "src-a"}, {"id": "  "}, {"resource": "/x.md"})
    assert source_ids(sources) == ("src-a", "", "")
