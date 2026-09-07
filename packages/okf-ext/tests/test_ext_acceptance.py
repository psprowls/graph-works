"""Corpus-wide properties for the `render` and `health` capabilities.

Separate from the per-branch unit suites for the reason okf-io's
`test_roundtrip.py` is separate from `test_document.py`: these assert
properties over a whole corpus, not the behaviour of one branch.

The two vendored bundles are the negative control. They are real OKF v0.2
bundles written by people who had never heard of these rules, so a finding here
is evidence the rule is wrong -- not evidence the bundle is.
"""

from __future__ import annotations

from datetime import date

import ext_helpers
import pytest
from okf_ext.health import health_rule
from okf_ext.render import escape_angle_brackets, render_rule
from okf_io import load_bundle, validate

#: `acme_retail/log.md`'s newest dated section is `2026-07-01`, so the health
#: finding set depends on the date the caller supplies. Both dates are asserted,
#: which is also the no-clock property: the rule reads `context.today` and
#: nothing else.
INSIDE_THE_GAP = date(2026, 7, 5)  # 4 days after the newest entry
OUTSIDE_THE_GAP = date(2026, 8, 6)  # 36 days after it


@pytest.fixture(params=["acme_retail", "ga4"])
def vendored(request):
    return load_bundle(ext_helpers.VENDORED_BUNDLES / request.param)


def house(bundle, rule, today):
    report = validate(bundle, today=today, extra_rules=[rule])
    return [f for f in report.findings if f.code.startswith(("render.", "health."))]


# --- render: silent on both vendored bundles --------------------------------


def test_render_produces_no_findings_at_all_on_a_vendored_bundle(vendored):
    """Neither bundle contains a wikilink or a callout, every table row in both
    is pipe-uniform, and the only angle-bracket content is `viz.html`, an asset
    the rule never parses."""
    assert house(vendored, render_rule(), OUTSIDE_THE_GAP) == []


# --- health: the exact finding set, at a pinned `today` ----------------------


def test_health_on_acme_retail_inside_the_gap():
    bundle = load_bundle(ext_helpers.ACME_RETAIL)
    found = house(bundle, health_rule(), INSIDE_THE_GAP)
    assert {(f.code, f.path) for f in found} == {
        ("health.uncited", "policies/margin-standard.md"),
        ("health.uncited", "policies/revenue-recognition.md"),
        ("health.uncited", "skills/run-on-bq.md"),
    }


def test_health_on_acme_retail_outside_the_gap():
    bundle = load_bundle(ext_helpers.ACME_RETAIL)
    found = house(bundle, health_rule(), OUTSIDE_THE_GAP)
    assert {(f.code, f.path) for f in found} == {
        ("health.uncited", "policies/margin-standard.md"),
        ("health.uncited", "policies/revenue-recognition.md"),
        ("health.uncited", "skills/run-on-bq.md"),
        ("health.log-gap", "log.md"),
    }
    gap = next(f for f in found if f.code == "health.log-gap")
    assert "2026-07-01" in gap.message
    assert "36 days" in gap.message


@pytest.mark.parametrize("today", [INSIDE_THE_GAP, OUTSIDE_THE_GAP])
def test_health_on_ga4_is_date_independent(today):
    """`ga4` carries no log, so the rule is silent about logs at either date --
    not an error, not a finding."""
    bundle = load_bundle(ext_helpers.GA4)
    found = house(bundle, health_rule(), today)
    assert {(f.code, f.path) for f in found} == {
        ("health.uncited", "datasets/ga4_obfuscated_sample_ecommerce.md"),
    }


# --- Neither rule ever makes a conformant bundle fail ------------------------


@pytest.mark.parametrize("rule_factory", [render_rule, health_rule])
def test_no_error_severity_finding_on_a_vendored_bundle(vendored, rule_factory):
    """`Report.ok` is a claim about OKF v0.2 conformance. At the default
    severity, adding either rule must not change it."""
    without = validate(vendored, today=OUTSIDE_THE_GAP)
    with_rule = validate(vendored, today=OUTSIDE_THE_GAP, extra_rules=[rule_factory()])
    assert with_rule.ok == without.ok is True


# --- The escape property, which is why both halves live in one capability ----

#: Placeholder-shaped prose of the kind a generator splices into a body.
PLACEHOLDERS = [
    "<slug>",
    "Replace <name> with the concept id.",
    "<a-b> and <c>",
    "Use <T> for the type parameter.",
    "leading text <placeholder>",
]


@pytest.mark.parametrize("text", PLACEHOLDERS)
def test_unescaped_placeholder_prose_is_flagged(tmp_path, text):
    """The control half of the property: without the escape, the rule fires.

    Count is not asserted -- `<a-b> and <c>` is two placeholders and two
    findings. What matters is that the unescaped form is never silent.
    """
    ext_helpers.write(tmp_path / "d.md", f"---\ntype: Note\ntitle: T\n---\n\n{text}\n")
    found = house(load_bundle(tmp_path), render_rule(), OUTSIDE_THE_GAP)
    assert found
    assert {f.code for f in found} == {"render.angle-bracket"}


@pytest.mark.parametrize("text", PLACEHOLDERS)
def test_escaped_placeholder_prose_is_never_flagged(tmp_path, text):
    """**The output of `escape_angle_brackets` never produces a
    `render.angle-bracket` finding.** This is the property that justifies
    shipping the preventive half inside the capability that detects the
    failure, and it is testable only because they are co-located."""
    ext_helpers.write(tmp_path / "d.md", f"---\ntype: Note\ntitle: T\n---\n\n{escape_angle_brackets(text)}\n")
    assert house(load_bundle(tmp_path), render_rule(), OUTSIDE_THE_GAP) == []
