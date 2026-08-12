from __future__ import annotations

from datetime import date

import ext_helpers
from okf_ext.placement import CODES, TOPIC, placement_rule
from okf_io import validate

TODAY = date(2026, 1, 1)

#: One literal map. No `SchemaSet` is imported anywhere in this file, and no
#: name here belongs to any real bundle -- design spec §7.1.
DIRECTORIES = {"Widget": "widgets/", "Crate": "crates/", "Shard": "crates/"}

#: The injected vocabulary: two types sharing one declared directory, which no
#: annotation can separate. `Crate` sits directly under it, `Shard` below that.
DEPTH = {"Crate": "exact", "Shard": "nested"}


def page(type_name, title, *, resource=None):
    lines = [f"type: {type_name}", f'title: "{title}"']
    if resource is not None:
        lines.append(f'resource: "{resource}"')
    return "---\n" + "\n".join(lines) + "\n---\n\n## Purpose\n\nA test page.\n"


def run(tmp_path, members, *, directories=DIRECTORIES, depth=DEPTH, severity="warn"):
    bundle = ext_helpers.write_bundle(tmp_path / "bundle", members)
    report = validate(
        bundle,
        today=TODAY,
        extra_rules=[placement_rule(directories, depth=depth, severity=severity)],
    )
    return report


def findings(report, code):
    return report.by_code(f"{TOPIC}.{code}")


# --- the catalog invariant --------------------------------------------------


def test_topic_is_placement_and_every_code_carries_the_prefix():
    assert TOPIC == "placement"
    assert CODES == ("placement.directory-mismatch", "placement.duplicate-resource")
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)
    # The module name IS the code prefix -- okf-io's own catalog invariant,
    # asserted mechanically rather than by eye.
    assert placement_rule.__module__.split(".")[-2] == TOPIC


# --- placement.directory-mismatch -------------------------------------------


def test_a_page_in_its_declared_directory_reports_nothing(tmp_path):
    report = run(tmp_path, {"widgets/a.md": page("Widget", "a")})
    assert not findings(report, "directory-mismatch")


def test_a_page_outside_its_declared_directory_is_flagged(tmp_path):
    report = run(tmp_path, {"crates/a.md": page("Widget", "a")})
    found = findings(report, "directory-mismatch")
    assert len(found) == 1
    assert found[0].path == "crates/a.md"
    assert found[0].spec == "okf_ext.placement"
    assert "widgets/" in found[0].message
    assert found[0].line == 2  # the `type:` key's own line


def test_an_exact_depth_page_below_depth_one_is_flagged(tmp_path):
    """`Crate` and `Shard` declare the same directory, so a plain prefix check
    passes this page. The depth map is what catches it."""
    report = run(tmp_path, {"crates/acme/nested.md": page("Crate", "nested")})
    assert [f.path for f in findings(report, "directory-mismatch")] == ["crates/acme/nested.md"]


def test_an_exact_depth_page_at_depth_one_reports_nothing(tmp_path):
    report = run(tmp_path, {"crates/acme.md": page("Crate", "acme")})
    assert not findings(report, "directory-mismatch")


def test_a_nested_page_at_depth_one_is_flagged(tmp_path):
    """The other half of the same shared prefix."""
    report = run(tmp_path, {"crates/loose.md": page("Shard", "loose")})
    assert [f.path for f in findings(report, "directory-mismatch")] == ["crates/loose.md"]


def test_a_nested_page_below_depth_one_reports_nothing(tmp_path):
    report = run(tmp_path, {"crates/acme/src/app.md": page("Shard", "app")})
    assert not findings(report, "directory-mismatch")


def test_without_a_depth_map_placement_is_a_plain_prefix_check(tmp_path):
    """`depth` defaults to empty, so a bundle whose types each own a directory
    outright never has to supply one."""
    bundle = ext_helpers.write_bundle(tmp_path / "bundle", {"crates/acme/nested.md": page("Crate", "nested")})
    report = validate(bundle, today=TODAY, extra_rules=[placement_rule(DIRECTORIES)])
    assert not findings(report, "directory-mismatch")


def test_a_type_absent_from_the_map_reports_nothing(tmp_path):
    """A type the caller declared nothing for is content, not a violation --
    and a type with no schema at all is already `schemas.no-schema-for-type`."""
    report = run(tmp_path, {"widgets/a.md": page("Gadget", "a")})
    assert not findings(report, "directory-mismatch")


def test_a_blank_directory_value_reports_nothing(tmp_path):
    report = run(tmp_path, {"widgets/a.md": page("Widget", "a")}, directories={"Widget": "   "})
    assert not findings(report, "directory-mismatch")


def test_a_blank_type_is_skipped(tmp_path):
    report = run(tmp_path, {"crates/blank.md": '---\ntype: "   "\ntitle: "blank"\n---\n\n## Purpose\n\nText.\n'})
    assert not findings(report, "directory-mismatch")


def test_an_unparseable_page_is_skipped(tmp_path):
    """okf-io's never-raise content model: a malformed concept still yields a
    `Document`, carrying its failure in `parse_error`. Re-reporting it here
    would duplicate `frontmatter.*`."""
    members = {"crates/broken.md": "---\ntype: Widget\n  bad: [unclosed\n---\n\n## Purpose\n\nText.\n"}
    bundle = ext_helpers.write_bundle(tmp_path / "bundle", members)
    # Assert the fixture really is unparseable, so a future okf-io that accepts
    # this YAML makes the test fail loudly instead of quietly testing nothing --
    # the page is in the wrong directory on purpose.
    assert bundle.concepts["crates/broken"].parse_error is not None
    report = validate(bundle, today=TODAY, extra_rules=[placement_rule(DIRECTORIES)])
    assert not findings(report, "directory-mismatch")


# --- placement.duplicate-resource -------------------------------------------


def test_two_pages_claiming_one_resource_report_the_loser(tmp_path):
    report = run(
        tmp_path,
        {
            "widgets/alpha.md": page("Widget", "alpha", resource="thing:dup"),
            "widgets/beta.md": page("Widget", "beta", resource="thing:dup"),
        },
    )
    found = findings(report, "duplicate-resource")
    assert len(found) == 1
    assert found[0].path == "widgets/beta.md"  # `alpha` sorts first, so find-by-resource keeps it
    assert "widgets/alpha.md" in found[0].message


def test_three_pages_claiming_one_resource_report_both_losers(tmp_path):
    report = run(
        tmp_path,
        {f"widgets/{name}.md": page("Widget", name, resource="thing:dup") for name in ("alpha", "beta", "gamma")},
    )
    assert [f.path for f in findings(report, "duplicate-resource")] == ["widgets/beta.md", "widgets/gamma.md"]


def test_a_page_with_no_resource_is_not_a_duplicate(tmp_path):
    report = run(
        tmp_path,
        {"widgets/alpha.md": page("Widget", "alpha"), "widgets/beta.md": page("Widget", "beta")},
    )
    assert not findings(report, "duplicate-resource")


# --- severity ---------------------------------------------------------------


def test_severity_defaults_to_warn(tmp_path):
    """The tier-2 house rule: `Report.ok` is a claim about OKF v0.2
    conformance, and a page sitting somewhere unexpected is a legal page
    (ADR-0012). A caller whose own reconciler cannot recover asks for `error`."""
    bundle = ext_helpers.write_bundle(tmp_path / "bundle", {"crates/a.md": page("Widget", "a")})
    report = validate(bundle, today=TODAY, extra_rules=[placement_rule(DIRECTORIES)])
    assert findings(report, "directory-mismatch")[0].severity == "warn"
    assert report.ok


def test_severity_is_overridable(tmp_path):
    report = run(tmp_path, {"crates/a.md": page("Widget", "a")}, severity="error")
    assert findings(report, "directory-mismatch")[0].severity == "error"
    assert not report.ok


# --- the code-set property --------------------------------------------------


def test_every_declared_code_fires_in_one_walk(tmp_path):
    """What a nonconformant-vault golden buys elsewhere: one built bundle, an
    asserted code set, and no golden to regenerate."""
    report = run(
        tmp_path,
        {
            "crates/a.md": page("Widget", "a", resource="thing:dup"),
            "widgets/a.md": page("Widget", "a", resource="thing:dup"),
        },
    )
    fired = {f.code for f in report.findings if f.code.startswith(f"{TOPIC}.")}
    assert fired == set(CODES)
