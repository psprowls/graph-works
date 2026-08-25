from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest
from okf_io import bundle
from okf_io.validate import Finding, Report, RuleContext, Severity, _registry, validate

TODAY = date(2026, 8, 3)
CONCEPT = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# Definition\n"


def make(tmp_path: Path, files: dict[str, str]) -> bundle.Bundle:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return bundle.load(tmp_path)


def finding(code: str, severity: Severity = "warn", path: str = "a.md", line: int | None = None):
    return Finding(code, severity, f"message for {code}", "§0", path, line)


def test_a_finding_is_json_serializable_with_no_encoder():
    """`Severity` is a Literal, not an Enum -- the habit `fm_data(dates="iso")` set."""
    payload = json.dumps(dataclasses.asdict(finding("x.one")))
    assert json.loads(payload)["severity"] == "warn"


def test_report_views_are_derived_not_stored():
    report = Report((finding("x.a", "error"), finding("x.b", "warn")))
    assert [f.code for f in report.errors] == ["x.a"]
    assert [f.code for f in report.warnings] == ["x.b"]
    assert report.ok is False
    assert Report((finding("x.b"),)).ok is True
    assert report.by_code("x.b") == (report.findings[1],)


def test_today_is_required_and_keyword_only(tmp_path):
    loaded = make(tmp_path, {"a.md": CONCEPT})
    with pytest.raises(TypeError):
        validate(loaded)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        validate(loaded, TODAY)  # type: ignore[misc]


def test_findings_are_sorted_independently_of_rule_order(tmp_path):
    loaded = make(tmp_path, {"a.md": CONCEPT})

    def late(ctx: RuleContext):
        yield finding("zzz.b", path="b.md")

    def early(ctx: RuleContext):
        yield finding("zzz.a", path="a.md")

    report = validate(loaded, today=TODAY, extra_rules=[late, early])
    assert [f.code for f in report.findings] == ["zzz.a", "zzz.b"]


def test_strict_promotes_every_warning(tmp_path):
    loaded = make(tmp_path, {"a.md": CONCEPT})

    def rule(ctx: RuleContext):
        yield finding("zzz.a", "warn")
        yield finding("zzz.b", "error")

    report = validate(loaded, today=TODAY, extra_rules=[rule], strict=True)
    assert {f.severity for f in report.findings} == {"error"}
    assert report.ok is False
    assert [f.code for f in report.findings] == ["zzz.a", "zzz.b"]


def test_an_external_rule_is_indistinguishable_from_a_builtin(tmp_path):
    """The plugin surface is proven here, before `okf-schemas` exists to consume it.

    Shape-identical is not enough to prove indistinguishable -- a pipeline that
    appended external findings after everyone else's would also pass a
    field-shape check. The real test is that the external finding is sorted
    *into* the run by the same key as every other finding, landing between two
    findings from a second, unrelated rule rather than merely after them.
    """
    loaded = make(tmp_path, {"a.md": CONCEPT})

    def rule(ctx: RuleContext):
        assert isinstance(ctx.today, date)
        assert ctx.bundle is loaded
        assert ctx.links.bodies.keys() == {"a"}
        yield finding("schema.kind-unknown", "error", path="a.md", line=5)

    def other(ctx: RuleContext):
        yield finding("schema.zzz-after", "warn", path="a.md", line=5)
        yield finding("schema.aaa-before", "warn", path="a.md", line=1)

    report = validate(loaded, today=TODAY, extra_rules=[rule, other])
    external = report.by_code("schema.kind-unknown")[0]
    assert isinstance(external, Finding)
    assert (external.path, external.line) == ("a.md", 5)
    assert external.message == "message for schema.kind-unknown"
    assert external.spec == "§0"
    assert external.severity == "error"
    # Sorted between `other`'s two findings, not appended after both of them.
    assert [f.code for f in report.findings] == [
        "schema.aaa-before",
        "schema.kind-unknown",
        "schema.zzz-after",
    ]


def test_an_external_rule_may_not_claim_a_builtin_prefix(tmp_path):
    loaded = make(tmp_path, {"a.md": CONCEPT})

    def rule(ctx: RuleContext):
        yield finding("links.broken", "error")

    with pytest.raises(ValueError, match="links"):
        validate(loaded, today=TODAY, extra_rules=[rule])


def test_an_exception_from_a_rule_propagates(tmp_path):
    """§11 tolerance is about bundle content, not about buggy plugins."""
    loaded = make(tmp_path, {"a.md": CONCEPT})

    def rule(ctx: RuleContext):
        raise RuntimeError("plugin bug")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="plugin bug"):
        validate(loaded, today=TODAY, extra_rules=[rule])


def test_the_registry_is_loaded_lazily():
    """The one inversion of the downward dependency rule, contained to one function.

    Imported by name rather than through `okf_io.validate`: a later task exports the
    `validate` *function* from the package, which shadows the submodule
    attribute, so `from okf_io import validate as m` would bind the function.
    """
    rules, topics = _registry()
    assert isinstance(rules, tuple)
    assert topics == {
        "computation",
        "frontmatter",
        "identity",
        "legacy",
        "lifecycle",
        "links",
        "provenance",
        "reserved",
        "trust",
    }
