from __future__ import annotations

from datetime import date

import pytest
from doc_wiki_okf.sources import DRAIN_CODES, drain_rule, drop_only_ledger
from drain_helpers import ENTRY_KEYS, source, wiki
from okf_io import validate

TODAY = date(2026, 9, 25)
MEMBER = "sources/2026-09-s.md"


def _codes(bundle, **kwargs):
    report = validate(bundle, today=TODAY, extra_rules=[drain_rule(ENTRY_KEYS)], **kwargs)
    return sorted((f.code, f.severity, f.path) for f in report.findings if f.code.startswith("sources."))


def test_no_ledger_is_one_undrained_warning_with_counts(tmp_path) -> None:
    bundle = wiki(tmp_path, source())
    report = validate(bundle, today=TODAY, extra_rules=[drain_rule(ENTRY_KEYS)])
    [finding] = report.by_code("sources.undrained")
    assert finding.severity == "warn" and finding.path == MEMBER
    assert "0/3 dispositioned" in finding.message and "pending: 1, 2, 3" in finding.message


def test_each_error_code_fires_at_error(tmp_path) -> None:
    other = (
        "---\ntype: Adr\ntitle: B\ndescription: d\nstatus: accepted\ndecisions:\n"
        "  - id: D1\n    claim: x.\n---\n\n## Decision\nd\n"
    )
    ledger = (
        "drain:\n  - claim: 1\n    landed: [/adrs/a.md#D9]\n"
        "  - claim: 2\n    landed: [/adrs/b.md#D1]\n"
        "  - claim: 7\n    dropped: history\n"
    )
    found = _codes(wiki(tmp_path, source(drain=ledger), **{"adrs/b.md": other}))
    assert ("sources.drain-dangling", "error", MEMBER) in found
    assert ("sources.drain-uncited", "error", MEMBER) in found
    assert ("sources.drain-invalid", "error", MEMBER) in found


def test_a_drained_source_reports_nothing(tmp_path) -> None:
    ledger = (
        "drain:\n  - claim: 1\n    landed: [/adrs/a.md#D1]\n"
        "  - claim: 2\n    landed: [/adrs/a.md#D2]\n"
        "  - claim: 3\n    dropped: history\n"
    )
    assert _codes(wiki(tmp_path, source(drain=ledger))) == []


def test_scope_is_honoured(tmp_path) -> None:
    bundle = wiki(tmp_path, source(), **{"sources/2026-09-t.md": source()})
    found = _codes(bundle, scope=frozenset({"sources/2026-09-t.md"}))
    assert [path for _c, _s, path in found] == ["sources/2026-09-t.md"]


def test_an_unrecognised_severity_undrained_raises() -> None:
    with pytest.raises(ValueError, match="severity_undrained"):
        drain_rule(ENTRY_KEYS, severity_undrained="urgent")  # type: ignore[arg-type]


def test_codes_are_the_sources_topic() -> None:
    assert DRAIN_CODES == (
        "sources.undrained",
        "sources.drain-invalid",
        "sources.drain-dangling",
        "sources.drain-uncited",
    )


def test_drop_only_ledger_accepts_dropped_entries() -> None:
    expected = ({"claim": 2, "dropped": "history"},)
    assert drop_only_ledger([{"claim": 2, "dropped": "history"}], items=3) == (expected, None)


def test_drop_only_ledger_absent_is_silent() -> None:
    assert drop_only_ledger(None, items=3) == (None, None)


def test_drop_only_ledger_rejects_landed_range_dupes_and_enum() -> None:
    for bad in (
        [{"claim": 1, "landed": ["/adrs/a.md#D1"]}],
        [{"claim": 4, "dropped": "history"}],
        [{"claim": 1, "dropped": "history"}, {"claim": 1, "dropped": "evidence"}],
        [{"claim": 1, "dropped": "boring"}],
        "oops",
    ):
        ledger, why = drop_only_ledger(bad, items=3)
        assert ledger is None and why


def test_drop_only_ledger_honours_a_narrowed_reason_allowlist() -> None:
    narrowed = ("history", "evidence")
    ledger, why = drop_only_ledger([{"claim": 1, "dropped": "duplicate"}], items=3, reasons=narrowed)
    assert ledger is None
    assert why is not None and "'duplicate'" in why and "history, evidence" in why
    ok = drop_only_ledger([{"claim": 1, "dropped": "evidence"}], items=3, reasons=narrowed)
    assert ok == (({"claim": 1, "dropped": "evidence"},), None)


def test_drop_only_ledger_defaults_to_every_drop_reason() -> None:
    ledger, why = drop_only_ledger([{"claim": 1, "dropped": "duplicate"}], items=3)
    assert why is None and ledger == ({"claim": 1, "dropped": "duplicate"},)
