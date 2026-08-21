from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import ClassVar

from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)


def codes(tmp_path: Path, files: dict[str, str], raw: dict[str, bytes] | None = None):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for rel, payload in (raw or {}).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    report = validate(bundle.load(tmp_path), today=TODAY)
    return report, {f.code for f in report.findings}


FULL = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# Definition\n"


def test_no_block_at_all(tmp_path):
    report, found = codes(tmp_path, {"a.md": "# Definition\n\nNo frontmatter.\n"})
    assert "frontmatter.missing" in found
    assert report.by_code("frontmatter.missing")[0].severity == "error"
    assert "frontmatter.missing-type" not in found
    assert "frontmatter.title-recommended" not in found


def test_an_empty_block_is_a_block(tmp_path):
    """`---\\n---` has a block; it is missing a type, not missing a block."""
    _, found = codes(tmp_path, {"a.md": "---\n---\n\n# Definition\n"})
    assert "frontmatter.missing" not in found
    assert "frontmatter.missing-type" in found


def test_unparseable_carries_the_kind_and_the_line(tmp_path):
    report, found = codes(tmp_path, {"a.md": '---\ntype: "unterminated\n---\n\n# D\n'})
    assert "frontmatter.unparseable" in found
    finding = report.by_code("frontmatter.unparseable")[0]
    assert finding.severity == "error"
    assert "yaml" in finding.message
    assert finding.line is not None


def test_an_unterminated_block_is_unparseable_not_missing(tmp_path):
    _, found = codes(tmp_path, {"a.md": "---\ntype: Metric\n\n# D\n"})
    assert found & {"frontmatter.unparseable"}
    assert "frontmatter.missing" not in found


def test_a_member_nobody_can_decode(tmp_path):
    report, found = codes(tmp_path, {"good.md": FULL}, raw={"bad.md": b"---\ntype: Metric\n---\n\n\xff\xfe\n"})
    assert "frontmatter.unreadable" in found
    finding = report.by_code("frontmatter.unreadable")[0]
    assert finding.severity == "error"
    assert finding.path == "bad.md"


def test_missing_type_covers_absent_empty_and_whitespace(tmp_path):
    for text in (
        "---\ntitle: T\n---\n\n# D\n",
        '---\ntype: ""\n---\n\n# D\n',
        '---\ntype: "  "\n---\n\n# D\n',
    ):
        _, found = codes(tmp_path, {"a.md": text})
        assert "frontmatter.missing-type" in found


def test_the_two_recommendations_fire_independently(tmp_path):
    _, found = codes(tmp_path, {"a.md": "---\ntype: Metric\ntitle: T\n---\n\n# D\n"})
    assert "frontmatter.description-recommended" in found
    assert "frontmatter.title-recommended" not in found


def test_a_complete_concept_produces_no_frontmatter_finding(tmp_path):
    _, found = codes(tmp_path, {"a.md": FULL})
    assert not {c for c in found if c.startswith("frontmatter.")}


def test_a_wrong_shaped_reserved_key_is_reported(tmp_path):
    report, found = codes(tmp_path, {"a.md": "---\ntype: Metric\ntitle: T\ndescription: D\nsources: 7\n---\n\n# D\n"})
    assert "frontmatter.value-malformed" in found
    finding = report.by_code("frontmatter.value-malformed")[0]
    assert finding.severity == "warn"
    assert "`sources`" in finding.message
    assert "int" in finding.message


def test_a_well_formed_sources_produces_no_value_malformed_finding(tmp_path):
    text = "---\ntype: Metric\ntitle: T\ndescription: D\nsources:\n  - resource: table:x\n---\n\n# D\n"
    _, found = codes(tmp_path, {"a.md": text})
    assert "frontmatter.value-malformed" not in found


def test_per_entry_granularity_yields_one_finding_per_path(tmp_path):
    text = "---\ntype: Metric\ntitle: T\ndescription: D\ntags: analytics\nsources: 7\n---\n\n# D\n"
    report, found = codes(tmp_path, {"a.md": text})
    assert "frontmatter.value-malformed" in found
    messages = {f.message for f in report.by_code("frontmatter.value-malformed")}
    assert len(report.by_code("frontmatter.value-malformed")) == 2
    assert any("`tags`" in m for m in messages)
    assert any("`sources`" in m for m in messages)


def test_a_nested_path_names_the_full_dotted_path(tmp_path):
    text = (
        "---\ntype: Metric\ntitle: T\ndescription: D\n"
        "sources:\n  - resource: table:x\n    usage_count: many\n---\n\n# D\n"
    )
    report, found = codes(tmp_path, {"a.md": text})
    assert "frontmatter.value-malformed" in found
    finding = report.by_code("frontmatter.value-malformed")[0]
    assert "`sources.0.usage_count`" in finding.message
    assert "str" in finding.message


def test_a_container_shape_is_named_in_yaml_terms_never_as_a_ruamel_class(tmp_path):
    """`fm_raw` is a round-trip tree, so a mapping there is a `CommentedMap`
    and a sequence a `CommentedSeq`. Naming the class would put ruamel's
    implementation into a message a human reads beside a spec citation --
    and `_yaml.py` already treats the emitter as an unstable contract, so
    the class names are not ours to publish either."""
    text = (
        "---\ntype: Metric\ntitle: T\ndescription: D\n"
        "generated: [1, 2]\nstale_after: {x: 1}\nverified: true\n---\n\n# D\n"
    )
    report, _ = codes(tmp_path, {"a.md": text})
    shapes = {
        finding.message.split("(")[1].split(")")[0]
        for finding in report.by_code("frontmatter.value-malformed")
        if "(" in finding.message
    }
    assert shapes == {"sequence", "mapping", "bool"}
    assert not any("Commented" in f.message for f in report.findings)


def test_per_shape_hole_regression_list_at_is_silent_from_trust_but_not_here(tmp_path):
    list_text = "---\ntype: Metric\ntitle: T\ndescription: D\ngenerated: {by: 'human:j', at: [1, 2]}\n---\n\n# D\n"
    _, list_found = codes(tmp_path, {"a.md": list_text})
    assert "frontmatter.value-malformed" in list_found
    assert "trust.timestamp-not-iso" not in list_found

    string_text = "---\ntype: Metric\ntitle: T\ndescription: D\ngenerated: {by: 'human:j', at: yesterday}\n---\n\n# D\n"
    _, string_found = codes(tmp_path, {"a.md": string_text})
    assert "frontmatter.value-malformed" in string_found
    assert "trust.timestamp-not-iso" in string_found


def test_deliberate_overlap_with_lifecycle_stale_after_malformed(tmp_path):
    text = "---\ntype: Metric\ntitle: T\ndescription: D\nstale_after: soonish\n---\n\n# D\n"
    _, found = codes(tmp_path, {"a.md": text})
    assert "lifecycle.stale-after-malformed" in found
    assert "frontmatter.value-malformed" in found


def test_extension_keys_never_fire_the_rule(tmp_path):
    text = (
        "---\ntype: Metric\ntitle: T\ndescription: D\nmy_ext: {a: 1}\nnot: [1, 2]\nfrom: {x: y}\nclass: 3\n---\n\n# D\n"
    )
    _, found = codes(tmp_path, {"a.md": text})
    assert "frontmatter.value-malformed" not in found


def test_an_unparseable_block_produces_no_value_malformed_finding(tmp_path):
    _, found = codes(tmp_path, {"a.md": '---\ntype: "unterminated\n---\n\n# D\n'})
    assert "frontmatter.value-malformed" not in found


def test_resolver_fallback_omits_the_parenthetical_when_unresolvable(tmp_path, monkeypatch):
    from okf_io._rules import frontmatter as frontmatter_rules

    class _FakeFrontmatter:
        coercion_failures = frozenset({"ghost.path"})

    class _FakeDocument:
        fm = _FakeFrontmatter()
        fm_raw: ClassVar[dict] = {}
        parse_error = None
        has_frontmatter = True

    class _FakeCtx:
        pass

    monkeypatch.setattr(frontmatter_rules, "concepts", lambda ctx: [("ghost", _FakeDocument())])
    findings = list(frontmatter_rules.coerced_values(_FakeCtx()))
    assert len(findings) == 1
    assert findings[0].message == "`ghost.path` is not readable as its declared type; the value was dropped"


def test_resolver_fallback_on_an_out_of_range_index(tmp_path, monkeypatch):
    from okf_io._rules import frontmatter as frontmatter_rules

    class _FakeFrontmatter:
        coercion_failures = frozenset({"sources.5"})

    class _FakeDocument:
        fm = _FakeFrontmatter()
        fm_raw: ClassVar[dict] = {"sources": ["a", "b"]}
        parse_error = None
        has_frontmatter = True

    class _FakeCtx:
        pass

    monkeypatch.setattr(frontmatter_rules, "concepts", lambda ctx: [("ghost", _FakeDocument())])
    findings = list(frontmatter_rules.coerced_values(_FakeCtx()))
    assert len(findings) == 1
    assert findings[0].message == "`sources.5` is not readable as its declared type; the value was dropped"
