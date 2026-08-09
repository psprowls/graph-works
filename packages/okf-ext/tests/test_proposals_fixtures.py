"""The corpus is what the suites believe it is, and adopting it costs no finding."""

from __future__ import annotations

from datetime import date

import ext_helpers
from okf_io import validate

TODAY = date(2026, 8, 8)


def test_the_whole_corpus_loads():
    bundle = ext_helpers.proposed_bundle()
    assert not bundle.unreadable


def test_every_proposal_on_disk_is_accounted_for():
    """A fixture added without a line in `PROPOSED_EXPECTED` fails here rather
    than silently joining a corpus nobody can explain."""
    bundle = ext_helpers.proposed_bundle()
    on_disk = {cid for cid in bundle.concepts if cid.startswith("proposals/")}
    assert on_disk == set(ext_helpers.PROPOSED_EXPECTED)


def test_adopting_the_capability_costs_no_finding():
    """Spec §5's zero-`validate()`-impact promise, asserted over the corpus
    rather than argued. `page_status` and `target` are extension keys and a
    proposal carries no OKF `status`, so nothing in the catalog has anything to
    say about a `proposals/` member -- including the deliberately malformed
    ones, whose malformation is this capability's business and not okf-io's."""
    report = validate(ext_helpers.proposed_bundle(), today=TODAY)
    offenders = [
        f"{finding.code} {finding.path}" for finding in report.findings if "proposals/" in (finding.path or "")
    ]
    assert not offenders, offenders


def test_the_copy_is_byte_identical(tmp_path):
    root = ext_helpers.proposed_copy(tmp_path)
    assert ext_helpers.snapshot(root) == ext_helpers.snapshot(ext_helpers.PROPOSED)
