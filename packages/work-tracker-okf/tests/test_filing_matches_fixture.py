from datetime import date
from pathlib import Path

import pytest
from okf_ext.body import sections as body_sections
from okf_io import load_bundle, parse
from work_helpers import CONFORMANT_TODAY
from work_tracker_okf import IGNORE, load_items
from work_tracker_okf.filing import FilingPlan, FilingSeed, apply, plan_filing

_ON = date(2026, 3, 10)

#: One vault page per type, by slug. The round trip compares a freshly filed page
#: of each type against the vault's own.
_EXEMPLARS = {
    "Epic": "2026-03-01-epic-conformant-vault",
    "Feature": "2026-03-02-epic-feature-filing-writer",
    "Spike": "2026-03-03-spike-path-layout-questions",
    "Bug": "2026-03-04-bug-slug-prefix-mismatch",
    "TechDebt": "2026-03-05-tech-debt-retire-the-sidecar",
    "TestGap": "2026-03-06-test-gap-cover-the-upsert",
}


def _headings(text: str) -> list[str]:
    return [section.heading.strip() for section in body_sections(parse(text).body)]


def _vault_page(root: Path, slug: str) -> str:
    return (root / "work" / f"{slug}.md").read_text(encoding="utf-8")


def _plan(root: Path, section_set, type_name: str) -> FilingPlan:
    bundle = load_bundle(root, ignore=IGNORE)
    return plan_filing(
        root,
        load_items(bundle),
        FilingSeed(
            type=type_name,
            title="A freshly filed item",
            description="Filed by the writer, into a copy of the vault.",
            on=_ON,
            words="freshly filed item",
            affects=("packages/work-tracker-okf",),
            tags=("fixture",),
        ),
        section_set,
    )


@pytest.mark.parametrize(("type_name", "exemplar"), sorted(_EXEMPLARS.items()))
def test_filing_reproduces_the_shape_the_vault_shows(conformant_root: Path, section_set, type_name, exemplar) -> None:
    """The agreement that would be unenforceable if the vault and the writer
    lived in different children: "the writer produces what the fixture shows" is
    one child's internal consistency here.

    The filed page carries a subset of the vault page's keys — the vault's items
    have advanced, so they also carry `phase`, `effort`, `sources` and friends.
    The assertion is therefore on the *restricted* sequence: every key the writer
    emits appears in the vault page in the same relative order.
    """
    plan = _plan(conformant_root, section_set, type_name)
    assert plan.refusal is None, plan.diff()
    filed_text = apply(plan).read_text(encoding="utf-8")

    filed_keys = list(parse(filed_text).fm_data())
    vault_keys = list(parse(_vault_page(conformant_root, exemplar)).fm_data())
    assert [key for key in vault_keys if key in set(filed_keys)] == filed_keys


@pytest.mark.parametrize(("type_name", "exemplar"), sorted(_EXEMPLARS.items()))
def test_filing_reproduces_the_body_headings_the_vault_shows(
    conformant_root: Path, section_set, type_name, exemplar
) -> None:
    plan = _plan(conformant_root, section_set, type_name)
    filed_text = apply(plan).read_text(encoding="utf-8")
    assert _headings(filed_text) == _headings(_vault_page(conformant_root, exemplar))


def test_the_filed_page_joins_the_vaults_own_items(conformant_root: Path, section_set) -> None:
    plan = _plan(conformant_root, section_set, "Feature")
    apply(plan)
    items = load_items(load_bundle(conformant_root, ignore=IGNORE))
    assert plan.slug in {item.slug for item in items}
    assert plan.slug == f"{_ON.isoformat()}-feature-freshly-filed-item"


def test_the_vault_today_is_not_the_filing_day() -> None:
    """A guard on the fixture's own arithmetic: the vault's `today` and the
    filing day differ, so nothing in the round trip can pass by coincidence."""
    assert CONFORMANT_TODAY != _ON
