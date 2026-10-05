"""The proposal pool: lookup, placement, type recovery, and the refused-type rule."""

from __future__ import annotations

from datetime import date
from types import MappingProxyType

import pytest
from doc_wiki_okf.proposals.adr import adr_directory, is_adr
from doc_wiki_okf.proposals.pool import REFUSED_TYPE, PoolError, ProposalPool, proposal_pool, refused_type_rule
from okf_ext.proposals import Proposal, Refusal
from okf_ext.schemas import Proposables, ProposableType, ProposalGuidance, ProposalPromotion, load_schemas
from okf_io import load_bundle
from okf_io import validate as okf_validate
from proposal_helpers import schema_set

G = ProposalGuidance(summary="s", question="q?")


def _entry(name, directory, *, dated=False):
    promotion = ProposalPromotion(dated=True, frontmatter=MappingProxyType({})) if dated else None
    return ProposableType(name=name, directory=directory, guidance=G, promotion=promotion)


POOL = ProposalPool(
    Proposables(
        types=(
            _entry("Adr", "adrs/", dated=True),
            _entry("Bug", "work/"),
            _entry("Explanation", "docs/explanations/"),
            _entry("Feature", "work/"),
        ),
        locked=("Source",),
        refused=(("Runbook", "missing-guidance"),),
    )
)


def _proposal(target, target_type=None):
    return Proposal(
        member="proposals/p.md",
        concept_id="proposals/p",
        target=target,
        title="T",
        description="d",
        page_status="approved",
        raw_page_status="approved",
        sources=(),
        verified=(),
        target_type=target_type,
    )


def test_names_follow_the_pool_order():
    assert POOL.names == ("Adr", "Bug", "Explanation", "Feature")


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        ("Explanation", ("pool", "Explanation")),
        (" explanation ", ("pool", "Explanation")),
        ("source", ("locked", "Source")),
        ("RUNBOOK", ("refused", "Runbook")),
        ("how-to", ("unknown", "how-to")),
    ],
)
def test_lookup_is_case_insensitive_and_names_the_state(asked, expected):
    assert POOL.lookup(asked) == expected


@pytest.mark.parametrize(
    ("asked", "fragment"),
    [("Source", "x-okf-accept-proposals: false"), ("Runbook", "missing-guidance"), ("Nope", "expected one of")],
)
def test_getitem_refuses_what_the_pool_cannot_file_into(asked, fragment):
    with pytest.raises(PoolError) as caught:
        POOL[asked]
    assert fragment in str(caught.value)
    assert isinstance(caught.value, KeyError)


def test_target_for_is_undated_until_a_dated_type_is_promoted():
    assert POOL.target_for("Adr", "Bulk Write") == "adrs/bulk-write.md"
    assert POOL.target_for("Adr", "Bulk Write", on=date(2026, 10, 4)) == "adrs/2026-10-04-bulk-write.md"
    assert POOL.target_for("Explanation", "Why", on=date(2026, 10, 4)) == "docs/explanations/why.md"


def test_type_for_prefers_the_recorded_target_type():
    assert POOL.type_for(_proposal("work/x.md", "Bug")).name == "Bug"


def test_type_for_falls_back_to_a_uniquely_owned_directory():
    assert POOL.type_for(_proposal("docs/explanations/why.md")).name == "Explanation"


def test_type_for_refuses_a_shared_directory_naming_the_candidates():
    refusal = POOL.type_for(_proposal("work/x.md"))
    assert isinstance(refusal, Refusal) and refusal.kind == "malformed-proposal"
    assert "Bug, Feature" in refusal.detail


def test_type_for_refuses_an_undeclared_directory():
    refusal = POOL.type_for(_proposal("concepts/x.md"))
    assert isinstance(refusal, Refusal) and refusal.kind == "malformed-proposal"


def test_type_for_uses_the_longest_directory_even_when_the_parent_is_shared():
    pool = ProposalPool(
        Proposables(
            types=(_entry("Feature", "work/"), _entry("Bug", "work/"), _entry("Incident", "work/incidents/")),
            locked=(),
            refused=(),
        )
    )
    recovered = pool.type_for(_proposal("work/incidents/outage.md"))
    assert isinstance(recovered, ProposableType) and recovered.name == "Incident"


def test_type_for_does_not_match_a_directory_name_prefix():
    refusal = POOL.type_for(_proposal("adrs-extra/x.md"))
    assert isinstance(refusal, Refusal) and refusal.kind == "malformed-proposal"


def test_type_for_normalizes_the_recorded_type_before_recovering_it():
    recovered = POOL.type_for(_proposal("work/x.md", " bug "))
    assert isinstance(recovered, ProposableType) and recovered.name == "Bug"


def test_pool_error_has_a_plain_message():
    assert str(PoolError("plain message")) == "plain message"
    assert str(PoolError()) == ""


@pytest.mark.parametrize("recorded", ["Source", "Runbook", "Gone"])
def test_a_recorded_type_no_longer_in_the_pool_is_unavailable(recorded):
    refusal = POOL.type_for(_proposal("docs/explanations/why.md", recorded))
    assert isinstance(refusal, Refusal) and refusal.kind == "type-unavailable"


def test_the_shipped_doc_wiki_pool_builds_from_its_schemas():
    pool = proposal_pool(schema_set())
    assert pool.names == ("Adr", "Explanation", "HowTo", "Reference", "Tutorial")


def test_the_rule_reports_one_warning_per_refused_type(tmp_path):
    (tmp_path / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8")
    report = okf_validate(
        load_bundle(tmp_path), today=date(2026, 10, 4), extra_rules=[refused_type_rule(POOL.proposables)]
    )
    (finding,) = [f for f in report.findings if f.code == REFUSED_TYPE]
    assert finding.severity == "warn" and finding.path is None
    assert "Runbook" in finding.message and "missing-guidance" in finding.message


def test_the_rule_is_silent_under_a_scoped_validation(tmp_path):
    (tmp_path / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8")
    report = okf_validate(
        load_bundle(tmp_path),
        today=date(2026, 10, 4),
        extra_rules=[refused_type_rule(POOL.proposables)],
        scope=frozenset({"index.md"}),
    )
    assert not [f for f in report.findings if f.code == REFUSED_TYPE]


@pytest.mark.parametrize("reason", ["invalid-flag", "missing-guidance"])
def test_refused_type_warning_does_not_claim_an_invalid_flag_is_true(tmp_path, reason):
    (tmp_path / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8")
    report = okf_validate(
        load_bundle(tmp_path),
        today=date(2026, 10, 4),
        extra_rules=[refused_type_rule(Proposables(types=(), locked=(), refused=(("Memo", reason),)))],
    )
    (finding,) = report.by_code(REFUSED_TYPE)
    assert finding.message == f"Memo excluded from the proposal pool: {reason}"


def test_is_adr_needs_the_declared_directory_and_the_type():
    directory = adr_directory(schema_set())
    assert directory == "adrs/"
    assert is_adr("adrs/2026-08-12-two-layer", "Adr", directory=directory)
    assert not is_adr("docs/explanations/x", "Adr", directory=directory)
    assert not is_adr("adrs/x", "Explanation", directory=directory)
    assert not is_adr("adrs/x", "Adr", directory=None)


def test_adr_identity_follows_a_relocated_schema_directory(tmp_path):
    (tmp_path / "Adr.schema.json").write_text(
        '{"type": "object", "properties": {"type": {"const": "Adr"}}, "x-okf-directory": "decisions/"}',
        encoding="utf-8",
        newline="",
    )
    directory = adr_directory(load_schemas(tmp_path))
    assert directory == "decisions/"
    assert is_adr("decisions/x", " Adr ", directory=directory)
    assert not is_adr("adrs/x", "Adr", directory=directory)


def test_no_adr_schema_means_no_adr_directory(tmp_path):
    (tmp_path / "Explanation.schema.json").write_text(
        '{"type": "object", "x-okf-directory": "docs/explanations/"}', encoding="utf-8", newline=""
    )
    assert adr_directory(load_schemas(tmp_path)) is None
