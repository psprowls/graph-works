from __future__ import annotations

from datetime import date

import pytest
from ext_helpers import VOCABULARY, tagged_bundle
from okf_ext.context import DEFAULT_NORMALIZATION, ExtContext
from okf_ext.tags.vocabulary import CODES, TOPIC, load_vocabulary, vocabulary_rule
from okf_io import Finding, validate

TODAY = date(2026, 8, 4)


def report(**kwargs):
    vocab = load_vocabulary(VOCABULARY)
    return validate(tagged_bundle(), today=TODAY, extra_rules=[vocabulary_rule(vocab)], **kwargs)


def test_the_rule_runs_through_the_real_pipeline():
    """The plugin contract. If `extra_rules` will not take this, nothing else
    in the capability matters."""
    assert report().by_code("tags.unknown")


def test_unknown_tags_are_reported():
    codes = {f.path for f in report().by_code("tags.unknown")}
    assert "flow.md" in codes  # headline-metric
    assert "block.md" in codes  # ga4


def test_a_deprecated_tag_names_its_replacement():
    findings = report().by_code("tags.deprecated")
    assert findings
    assert all("metric" in f.message for f in findings)
    assert {f.path for f in findings} == {"block.md", "merge_me.md", "underscore.md"}


def test_a_non_canonical_tag_is_reported_as_such():
    findings = report().by_code("tags.non-canonical")
    assert {f.path for f in findings} == {"block.md", "underscore.md"}
    assert any("data-quality" in f.message for f in findings)


def test_a_non_canonical_tag_is_not_also_reported_unknown():
    """`Data Quality` has one problem, not two. Reporting both would double-
    count the same underlying mess and make the counts meaningless."""
    unknown = {(f.path, f.message) for f in report().by_code("tags.unknown")}
    assert not any("Data Quality" in message for _, message in unknown)


def test_every_emitted_code_is_a_member_of_codes():
    """`CODES` is exported alongside `TOPIC`, but the rule used to emit its
    own string literals (`"tags.unknown"`, etc.) with nothing tying them to
    `CODES` -- they agreed by construction, not by reference, so a future
    edit to one could silently drift from the other. `rule()` now unpacks
    its codes directly from `CODES` (see `vocabulary.py`); this is the
    second, independent guard: every code the rule can actually emit, over a
    corpus exercising all three, must be a member of `CODES`."""
    # Filtered to `TOPIC`'s own prefix: `report()` also carries built-in
    # findings (e.g. `frontmatter.unparseable` for the corpus's `broken.md`),
    # which are no part of this rule's contract and would otherwise mask a
    # missing code.
    codes = {finding.code for finding in report().findings if finding.code.startswith(f"{TOPIC}.")}
    assert codes, "the corpus must actually exercise at least one code"
    assert codes <= set(CODES)
    assert codes == {"tags.unknown", "tags.deprecated", "tags.non-canonical"}


def test_every_code_starts_with_the_topic_prefix():
    assert CODES
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)


def test_vocabulary_rule_accepts_ctx_positionally():
    """Every other function in the capability -- `inventory`, `clusters`,
    `plan_rename`, `plan_merge`, `plan_normalize`, `plan_from_vocabulary` --
    takes `ctx` positional-or-keyword. `vocabulary_rule` used to be the one
    exception, marking `ctx` keyword-only, which broke
    `vocabulary_rule(vocab, ctx)` even though every sibling accepts exactly
    that call shape. This pins the fix: no `*` before `ctx`."""
    vocab = load_vocabulary(VOCABULARY)
    ctx = ExtContext(normalization=DEFAULT_NORMALIZATION)
    vocabulary_rule(vocab, ctx)  # must not raise TypeError


def test_the_house_rule_never_changes_conformance():
    """`Report.ok` is SPEC.md's word to give, not okf-ext's. The corpus is
    already non-conformant (`broken.md` is unparseable), so this asserts the
    rule does not *change* the verdict rather than that the verdict is good.
    """
    vocab = load_vocabulary(VOCABULARY)
    without = validate(tagged_bundle(), today=TODAY)
    with_rule = validate(tagged_bundle(), today=TODAY, extra_rules=[vocabulary_rule(vocab)])
    assert with_rule.ok == without.ok


def test_a_conformant_bundle_stays_conformant_with_house_rule(tmp_path):
    """A bundle with no parse errors and no frontmatter issues should remain
    conformant (ok=True) even when carrying tags that violate the vocabulary."""
    from okf_io import load_bundle

    # Build a valid, conformant bundle that violates the vocabulary
    target = tmp_path / "valid.md"
    target.write_text(
        "---\ntype: Metric\ntitle: Valid\ndescription: Good frontmatter\n"
        "tags: [unknown_tag, kpi]\n---\n\n# Valid\n",
        encoding="utf-8",
    )
    vocab = load_vocabulary(VOCABULARY)
    result = validate(load_bundle(tmp_path), today=TODAY, extra_rules=[vocabulary_rule(vocab)])

    # The bundle should be conformant (no parse errors, valid frontmatter)
    assert result.ok is True

    # But the house rule should emit findings
    house_findings = [f for f in result.findings if f.code.startswith("tags.")]
    assert house_findings
    # Should find: unknown_tag as unknown, kpi as deprecated
    codes = {f.code for f in house_findings}
    assert "tags.unknown" in codes
    assert "tags.deprecated" in codes


def test_every_finding_is_a_warning():
    """All house rule findings are warnings. Each one is a stylistic
    suggestion, never a conformance failure."""
    findings = [f for f in report().findings if f.code.startswith("tags.")]
    assert findings
    assert {f.severity for f in findings} == {"warn"}


def test_findings_cite_the_vocabulary_as_their_spec():
    """`Finding.spec` answers "what says so", and for a house rule the honest
    answer is the house-rules file."""
    findings = [f for f in report().findings if f.code.startswith("tags.")]
    assert {f.spec for f in findings} == {"_tags.yaml"}


def test_strict_promotes_house_rules_with_no_second_namespace():
    findings = [f for f in report(strict=True).findings if f.code.startswith("tags.")]
    assert {f.severity for f in findings} == {"error"}


def test_findings_are_shape_identical_to_built_ins():
    built_in = [f for f in report().findings if not f.code.startswith("tags.")]
    house = [f for f in report().findings if f.code.startswith("tags.")]
    assert built_in and house
    assert all(isinstance(f, Finding) for f in house)
    assert all(f.path is not None and "." in f.code for f in house)


def test_a_duplicated_tag_is_reported_once_per_concept(tmp_path):
    target = tmp_path / "a.md"
    target.write_text(
        "---\ntype: Metric\ntitle: T\ndescription: D\ntags: [ga4, ga4]\n---\n\n# T\n",
        encoding="utf-8",
    )
    from okf_io import load_bundle

    vocab = load_vocabulary(VOCABULARY)
    found = validate(load_bundle(tmp_path), today=TODAY, extra_rules=[vocabulary_rule(vocab)])
    assert len(found.by_code("tags.unknown")) == 1


def test_claiming_a_built_in_prefix_raises():
    """Proves the collision guard is understood rather than assumed — and that
    `tags.` really is ours to claim."""

    def colliding(_ctx):
        yield Finding(
            code="trust.invented", severity="warn", message="m", spec="x", path=None, line=None
        )

    with pytest.raises(ValueError, match="topic prefix"):
        validate(tagged_bundle(), today=TODAY, extra_rules=[colliding])


# --- Probes for edge cases mentioned in the task ---


def test_probe_case_sensitive_non_canonical():
    """Non-canonical spellings are reported even when exact tag spelling differs.
    `Data Quality` (mixed case) is non-canonical; its canonical form
    `data-quality` is known and allowed, so only non-canonical is reported."""
    findings = report().by_code("tags.non-canonical")
    data_quality_findings = [f for f in findings if "Data Quality" in f.message]
    assert data_quality_findings, "Data Quality should be reported as non-canonical"


def test_probe_deprecated_with_replacement_vs_without():
    """Deprecated tags with replacements should mention the replacement in the message.
    The corpus vocabulary has `kpi` deprecated with `replaced_by: metric`."""
    findings = report().by_code("tags.deprecated")
    # All should mention the replacement
    for finding in findings:
        assert "metric" in finding.message, f"Expected replacement mentioned in: {finding.message}"


def test_probe_deprecated_with_no_replacement(tmp_path):
    """Deprecated tags with no replacement should explicitly state 'no replacement'."""
    from okf_io import load_bundle

    # Create a vocabulary with a deprecated tag that has no replacement
    vocab_file = tmp_path / "_tags.yaml"
    vocab_file.write_text(
        "version: 1\ntags:\n  - name: legacy\n    deprecated: true\n",
        encoding="utf-8",
    )

    # Create a document using the deprecated tag
    doc_file = tmp_path / "doc.md"
    doc_file.write_text(
        "---\ntype: Metric\ntitle: T\ndescription: D\ntags: [legacy]\n---\n\n# T\n",
        encoding="utf-8",
    )

    vocab = load_vocabulary(vocab_file)
    result = validate(load_bundle(tmp_path), today=TODAY, extra_rules=[vocabulary_rule(vocab)])

    findings = [f for f in result.findings if f.code == "tags.deprecated"]
    assert findings, "Should have a deprecated finding"
    assert all("no replacement" in f.message for f in findings), (
        "Should explicitly state no replacement"
    )


def test_probe_concept_with_parse_error_no_false_findings():
    """A concept with parse_error should produce zero tag findings.
    We must not report false things about documents we couldn't read."""
    broken_findings = [
        f for f in report().findings if f.path == "broken.md" and f.code.startswith("tags.")
    ]
    assert len(broken_findings) == 0, f"Expected zero findings for broken.md, got {broken_findings}"


def test_probe_empty_tags_list():
    """A concept with no tags should produce no findings.
    (This is normal and expected behavior.)"""
    untagged_findings = [
        f for f in report().findings if f.path == "untagged.md" and f.code.startswith("tags.")
    ]
    assert len(untagged_findings) == 0


def test_probe_tags_not_a_sequence_concept():
    """A concept with tags that is not a sequence (e.g., string) should
    produce no tag findings — we cannot read the tags."""
    scalar_findings = [
        f for f in report().findings if f.path == "scalar_tags.md" and f.code.startswith("tags.")
    ]
    assert len(scalar_findings) == 0, (
        f"Expected zero findings for scalar_tags.md, got {scalar_findings}"
    )


def test_probe_non_canonical_suppression_boundary_all_cases(tmp_path):
    """Comprehensive test of all five suppression cases:
    - Data Quality → non-canonical only
    - KPI (corpus) → non-canonical + deprecated
    - KPI (two-spellings) → non-canonical + deprecated (FIXED DEFECT)
    - E-Commerce → non-canonical + unknown
    - kpi (canonical) → deprecated only
    """
    from types import MappingProxyType

    from okf_ext.tags.model import Vocabulary
    from okf_io import load_bundle

    # Test first four cases with YAML fixture
    vocab_file = tmp_path / "_tags.yaml"
    vocab_file.write_text(
        "version: 1\ntags:\n"
        "  - name: metric\n"
        "  - name: data-quality\n"
        "  - name: kpi\n"
        "    deprecated: true\n"
        "    replaced_by: metric\n",
        encoding="utf-8",
    )

    doc_file = tmp_path / "doc.md"
    doc_file.write_text(
        "---\ntype: Metric\ntitle: T\ndescription: D\n"
        "tags: [Data Quality, KPI, E-Commerce, kpi]\n"
        "---\n\n# T\n",
        encoding="utf-8",
    )

    vocab = load_vocabulary(vocab_file)
    result = validate(load_bundle(tmp_path), today=TODAY, extra_rules=[vocabulary_rule(vocab)])
    findings = [f for f in result.findings if f.path == "doc.md" and f.code.startswith("tags.")]

    # Case 1: Data Quality → data-quality (allowed) → non-canonical only
    dq = [f for f in findings if "Data Quality" in f.message]
    dq_codes = {f.code for f in dq}
    assert dq_codes == {"tags.non-canonical"}, f"Case 1 Data Quality: {dq_codes}"

    # Case 2: KPI → kpi (deprecated, canonical form not allowed) → both
    kpi_corpus = [f for f in findings if f.message.startswith("Tag `KPI`")]
    kpi_corpus_codes = {f.code for f in kpi_corpus}
    assert kpi_corpus_codes == {
        "tags.non-canonical",
        "tags.deprecated",
    }, f"Case 2 KPI (corpus): {kpi_corpus_codes}"
    assert any("metric" in f.message for f in kpi_corpus if f.code == "tags.deprecated")

    # Case 3: (NEW) KPI exact spelling deprecated, kpi allowed
    # Build Vocabulary directly to create the two-spellings shape
    two_spellings_vocab = Vocabulary(
        allowed=frozenset(["kpi", "metric"]),
        deprecated=MappingProxyType({"KPI": "metric"}),
        descriptions=MappingProxyType({}),
        source="test-vocab.yaml",
    )

    doc3_file = tmp_path / "doc3.md"
    doc3_file.write_text(
        "---\ntype: Metric\ntitle: T\ndescription: D\ntags: [KPI]\n---\n\n# T\n",
        encoding="utf-8",
    )

    result3 = validate(
        load_bundle(tmp_path),
        today=TODAY,
        extra_rules=[vocabulary_rule(two_spellings_vocab)],
    )
    findings3 = [f for f in result3.findings if f.path == "doc3.md" and f.code.startswith("tags.")]
    codes3 = {f.code for f in findings3}
    assert codes3 == {
        "tags.non-canonical",
        "tags.deprecated",
    }, f"Case 3 KPI (two-spellings, FIXED): {codes3}"
    assert any("metric" in f.message for f in findings3 if f.code == "tags.deprecated")

    # Case 4: E-Commerce → e-commerce (unknown) → both
    ec = [f for f in findings if "E-Commerce" in f.message]
    ec_codes = {f.code for f in ec}
    assert ec_codes == {
        "tags.non-canonical",
        "tags.unknown",
    }, f"Case 4 E-Commerce: {ec_codes}"

    # Case 5: kpi → kpi (already canonical) → deprecated only
    kpi_canonical = [f for f in findings if "kpi" in f.message and "deprecated" in f.message]
    kpi_canonical_codes = {f.code for f in kpi_canonical}
    assert kpi_canonical_codes == {"tags.deprecated"}, (
        f"Case 5 kpi (canonical): {kpi_canonical_codes}"
    )
    assert any("metric" in f.message for f in kpi_canonical if f.code == "tags.deprecated")


def test_probe_unknown_with_suggestion():
    """Unknown tags that are close matches should suggest the match."""
    findings = report().by_code("tags.unknown")
    # metrics is a close match for metric
    metrics_findings = [f for f in findings if "metrics" in f.message]
    assert any("metric" in f.message for f in metrics_findings), (
        "Should suggest 'metric' for unknown tag 'metrics'"
    )


def test_probe_unknown_without_suggestion():
    """Unknown tags with no close match should not suggest anything."""
    findings = report().by_code("tags.unknown")
    # ga4 has no close match
    ga4_findings = [f for f in findings if "ga4" in f.message]
    assert ga4_findings, "Should have unknown finding for ga4"
    assert not any("Did you mean" in f.message for f in ga4_findings), (
        "Should not suggest anything for 'ga4'"
    )


def test_probe_unknown_suggestion_can_be_deprecated(tmp_path):
    """A suggestion for an unknown tag can itself be a deprecated tag."""
    from okf_io import load_bundle

    # Create a vocabulary where the suggested match is deprecated
    vocab_file = tmp_path / "_tags.yaml"
    vocab_file.write_text(
        "version: 1\ntags:\n"
        "  - name: metric\n"
        "  - name: kpi\n"
        "    deprecated: true\n"
        "    replaced_by: metric\n",
        encoding="utf-8",
    )

    doc_file = tmp_path / "doc.md"
    doc_file.write_text(
        "---\ntype: Metric\ntitle: T\ndescription: D\ntags: [kpis]\n---\n\n# T\n",
        encoding="utf-8",
    )

    vocab = load_vocabulary(vocab_file)
    result = validate(load_bundle(tmp_path), today=TODAY, extra_rules=[vocabulary_rule(vocab)])
    findings = [f for f in result.findings if f.code == "tags.unknown"]
    assert findings, "Should have unknown finding for kpis"
    # The suggestion is kpi, which is deprecated, but we still suggest it
    assert any("kpi" in f.message for f in findings), (
        "Should suggest 'kpi' even though it is deprecated"
    )
