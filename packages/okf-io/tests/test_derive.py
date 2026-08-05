from __future__ import annotations

import inspect
from datetime import UTC, date, datetime

import pytest
from helpers import BUNDLES, EDGE, read
from okf_io.derive import effective_status, is_stale, last_verified_at, trust_tier
from okf_io.document import Document


def fm_of(path):
    return Document.parse(read(path)).fm


def test_no_verified_is_unverified():
    assert trust_tier(fm_of(BUNDLES / "acme_retail/skills/run-on-bq.md")) == "unverified"


def test_human_actor_is_human_reviewed():
    assert trust_tier(fm_of(BUNDLES / "acme_retail/metrics/revenue.md")) == "human-reviewed"
    assert trust_tier(fm_of(EDGE / "verified_bare_mapping.md")) == "human-reviewed"


def test_process_actor_only_is_machine_confirmed():
    from okf_io.models import build_frontmatter
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"verified": [{"by": "process:nightly", "at": "2026-07-01T00:00:00Z"}]}))
    assert trust_tier(fm) == "machine-confirmed"


def test_effective_status_defaults_to_stable():
    assert effective_status(fm_of(EDGE / "footnotes_join.md")) == "stable"


def test_effective_status_returns_known_and_unknown_values_verbatim():
    from okf_io.models import build_frontmatter
    from ruamel.yaml.comments import CommentedMap

    assert effective_status(fm_of(BUNDLES / "acme_retail/metrics/gross-margin-legacy.md")) == "deprecated"
    fm = build_frontmatter(CommentedMap({"status": "under-review-by-legal"}))
    assert effective_status(fm) == "under-review-by-legal"


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 12, 30), False),
        (date(2026, 12, 31), True),  # boundary: today >= stale_after
        (date(2027, 1, 1), True),
    ],
)
def test_staleness_at_the_boundary(today, expected):
    fm = fm_of(BUNDLES / "acme_retail/metrics/revenue.md")
    assert fm.stale_after == date(2026, 12, 31)
    assert is_stale(fm, today=today) is expected


def test_absent_stale_after_is_never_stale():
    fm = fm_of(BUNDLES / "acme_retail/skills/run-on-bq.md")
    assert is_stale(fm, today=date(2099, 1, 1)) is False


def test_today_is_keyword_only_and_has_no_default():
    parameter = inspect.signature(is_stale).parameters["today"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_last_verified_at_picks_the_most_recent():
    fm = fm_of(EDGE / "verified_list.md")
    stamp = last_verified_at(fm)
    assert stamp is not None
    assert (stamp.year, stamp.month, stamp.day) == (2026, 7, 2)


def test_last_verified_at_is_none_without_verified():
    assert last_verified_at(fm_of(BUNDLES / "acme_retail/skills/run-on-bq.md")) is None


def test_last_verified_at_is_always_tz_aware():
    """A tie must not let YAML ordering decide the result's tz-awareness."""
    from okf_io.models import build_frontmatter
    from ruamel.yaml.comments import CommentedMap

    aware = {"by": "process:a", "at": "2026-07-02T00:00:00Z"}
    naive = {"by": "process:b", "at": "2026-07-02T00:00:00"}
    for order in ([aware, naive], [naive, aware]):
        fm = build_frontmatter(CommentedMap({"verified": order}))
        stamp = last_verified_at(fm)
        assert stamp is not None
        assert stamp.tzinfo is not None, f"naive leaked out for order {order}"
        # The whole point: safe to compare against an aware datetime.
        assert stamp == datetime(2026, 7, 2, tzinfo=UTC)


def test_uncoercible_stale_after_is_never_stale():
    """The plan's criterion says 'absent OR uncoercible'; only absent was covered."""
    from okf_io.models import build_frontmatter
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"stale_after": "not-a-date"}))
    assert fm.stale_after is None
    assert "stale_after" in fm.coercion_failures
    assert is_stale(fm, today=date.max) is False


def test_trust_tier_with_an_unattributed_entry():
    """Pins the documented reading rather than leaving it accidental."""
    from okf_io.models import build_frontmatter
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"verified": [{"at": "2026-07-01T00:00:00Z"}]}))
    assert fm.verified[0].by is None
    assert trust_tier(fm) == "machine-confirmed"


def test_empty_status_is_returned_verbatim():
    """`status: ""` is authored; only an absent status defaults."""
    from okf_io.models import build_frontmatter
    from ruamel.yaml.comments import CommentedMap

    assert effective_status(build_frontmatter(CommentedMap({"status": ""}))) == ""
    assert effective_status(build_frontmatter(CommentedMap({}))) == "stable"
