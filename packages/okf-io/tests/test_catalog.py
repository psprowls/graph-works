from __future__ import annotations

import dataclasses
import random
import re
from datetime import date
from pathlib import Path

import pytest
from helpers import BUNDLES, GOLDEN, NONCONFORMANT, render_finding, write
from okf_io import _md, _rules, bundle
from okf_io.validate import Report, RuleContext, validate

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PACKAGE_ROOT.parent.parent

#: Fixed, so `lifecycle.stale` fires as a function of the argument and nothing else.
GOLDEN_TODAY = date(2026, 8, 3)

#: Hand-written from the §8 catalog table, not derived from a run. Regenerating
#: the golden file cannot satisfy this.
ERROR_CODES = frozenset(
    {
        "frontmatter.missing",
        "frontmatter.unparseable",
        "frontmatter.unreadable",
        "frontmatter.missing-type",
        "identity.canonical-collision",
        "provenance.source-resource-missing",
        "trust.generated-by-missing",
        "lifecycle.status-unknown",
        "lifecycle.stale-after-malformed",
        "computation.runtime-missing",
        "computation.missing",
        "computation.duplicate",
        "reserved.index-frontmatter",
        "reserved.index-extra-keys",
        "reserved.log-heading-not-date",
    }
)

#: `identity.canonical-collision` requires two members whose raw disk ids are
#: NFC-equal but byte-different -- exactly what a normalization-folding
#: filesystem (APFS, this repo's dev machines) collapses into one directory
#: entry on write, so it cannot exist as two real files in this fixture. It
#: is exempted from "one walk triggers every code" and covered instead by
#: `test_rules_identity.py`'s synthetic-`Bundle` test.
NOT_REPRODUCIBLE_FROM_DISK = frozenset({"identity.canonical-collision"})


def test_not_reproducible_from_disk_is_a_subset_of_the_catalog():
    """A careless addition to this carve-out set should not silently widen
    it past codes the catalog actually declares."""
    assert NOT_REPRODUCIBLE_FROM_DISK <= _rules.CATALOG


#: The corpus's genuinely malformed members: no frontmatter block, unterminated
#: YAML, and a member that fails UTF-8 decoding entirely (so it never becomes a
#: `concepts` entry -- it only ever surfaces via `bundle.unreadable`).
MALFORMED_MEMBERS = (
    "concepts/no-frontmatter.md",
    "concepts/not-utf8.md",
    "concepts/unterminated.md",
)


@pytest.fixture(scope="module")
def golden_report() -> Report:
    """One walk over the corpus, shared by every test in this module.

    Pure and deterministic given the fixed ``GOLDEN_TODAY``, so re-running it
    per test would only re-walk 17 files and re-run the whole catalog for no
    reason. Do not reuse this fixture for two calls that must be independent
    runs -- see ``_fresh_golden_report`` below.
    """
    return validate(bundle.load(NONCONFORMANT), today=GOLDEN_TODAY)


def _fresh_golden_report() -> Report:
    """A fresh, independent walk-and-validate over the same corpus.

    Unlike ``golden_report``, this is never memoized: the two determinism
    tests need two runs that do not share a single ``Report`` instance, or
    the comparison would be an object compared with itself and pass no
    matter how nondeterministic the pipeline actually was.
    """
    return validate(bundle.load(NONCONFORMANT), today=GOLDEN_TODAY)


def test_the_corpus_triggers_every_catalog_code_except_the_disk_unreproducible_one(
    golden_report: Report,
) -> None:
    """One walk, every code except `NOT_REPRODUCIBLE_FROM_DISK` -- see its
    docstring for why that one cannot be a real on-disk fixture here."""
    assert {f.code for f in golden_report.findings} == _rules.CATALOG - NOT_REPRODUCIBLE_FROM_DISK


def test_the_golden_report_errors_match_the_reproducible_error_codes(golden_report: Report) -> None:
    """`ERROR_CODES` is fifteen (the declared catalog count); the golden
    corpus reproduces fourteen of them, since `identity.canonical-collision`
    is in `NOT_REPRODUCIBLE_FROM_DISK` and never fires from a real walk."""
    assert {f.severity for f in golden_report.errors} == {"error"}
    assert {f.code for f in golden_report.errors} == ERROR_CODES - NOT_REPRODUCIBLE_FROM_DISK


def test_the_severity_split_matches_the_catalog(golden_report: Report) -> None:
    assert len(ERROR_CODES) == 15
    assert len(_rules.CATALOG - ERROR_CODES) == 16
    assert {f.code for f in golden_report.warnings} == _rules.CATALOG - ERROR_CODES


def test_the_report_matches_the_reviewed_golden_file(golden_report: Report) -> None:
    rendered = "\n".join(render_finding(f) for f in golden_report.findings) + "\n"
    assert rendered == GOLDEN.read_text(encoding="utf-8")


def test_a_malformed_concept_does_not_stop_the_others(golden_report: Report) -> None:
    """§11: a malformed or unreadable member does not stop the walk.

    For each of the three genuinely malformed members -- ``no-frontmatter.md``
    (no frontmatter block), ``not-utf8.md`` (fails UTF-8 decoding, so it is not
    even a ``concepts`` entry), and ``unterminated.md`` (unterminated YAML) --
    this asserts some finding's path sorts strictly after it. If a rule's
    iteration silently stopped at any one of them instead of continuing past
    it, nothing after it would ever be reported and this assertion would fail
    on its own, independent of the byte-for-byte golden comparison.
    """
    paths = {f.path for f in golden_report.findings if f.path is not None}
    for member in MALFORMED_MEMBERS:
        assert any(path > member for path in paths), (
            f"no finding sorts after {member!r}; the walk may have stopped there"
        )


def test_every_declared_code_carries_its_module_prefix():
    """The `_rules` module name *is* the code prefix, asserted mechanically.

    Static: it needs no fixture, so the catalog cannot drift from its own file
    layout even before a rule has a test.
    """
    for topic, codes in _rules.CODES_BY_TOPIC.items():
        for code in codes:
            assert code.startswith(f"{topic}."), (topic, code)


def test_the_catalog_is_thirty_one_codes_across_nine_topics():
    assert len(_rules.CATALOG) == 31
    assert len(_rules.TOPICS) == 9
    assert sum(len(codes) for codes in _rules.CODES_BY_TOPIC.values()) == 31


@pytest.mark.parametrize("claude_md", [_PACKAGE_ROOT / "AGENTS.md"], ids=["okf-io"])
def test_the_catalog_counts_the_agent_docs_quote_are_the_catalogs_own(claude_md) -> None:
    """`packages/okf-io/AGENTS.md` — the file an agent working in this package
    reads first, and the one its `CLAUDE.md` just `@`-imports — states the
    catalog's size in prose, and it said `19 rule functions, 29 codes` for the
    whole life of the rule that made it twenty and thirty. Nothing read it, so
    nothing noticed.

    Only that file is pinned. The root `AGENTS.md` deliberately quotes no
    number: it delegates to this one in as many words ("check there rather
    than trusting a number restated here, since it has drifted before without
    anything noticing"), and asserting a count there would contradict the
    instruction the file itself gives.

    Counted, not spelled: the assertion reads whatever number sits in front of
    each noun, so rewording the sentence keeps working and only the numbers are
    pinned. `test_the_catalog_is_thirty_codes_across_eight_topics` pins the
    catalog itself; this pins what the files an agent reads first say about it.
    """
    text = claude_md.read_text(encoding="utf-8")
    expected = {
        "rule functions": len(_rules.RULES),
        "codes": len(_rules.CATALOG),
        "topics": len(_rules.TOPICS),
    }
    for noun, count in expected.items():
        quoted = {int(found) for found in re.findall(rf"(\d+) {re.escape(noun)}", text)}
        assert quoted, f"{claude_md.name} states no '<n> {noun}'"
        assert quoted == {count}, f"{claude_md.name} says {sorted(quoted)} {noun}; the catalog has {count}"


def test_no_rule_emits_an_undeclared_code(golden_report: Report) -> None:
    assert {f.code for f in golden_report.findings} <= _rules.CATALOG


def test_every_declared_code_is_actually_emitted(golden_report: Report) -> None:
    """A code declared but never produced by a real walk is a catalog entry
    nothing on disk can implement, except the one carved out above."""
    assert {f.code for f in golden_report.findings} >= _rules.CATALOG - NOT_REPRODUCIBLE_FROM_DISK


@pytest.mark.parametrize("name", ["acme_retail", "ga4"])
def test_the_vendored_bundles_report_zero_errors(name):
    """Worth more than any golden file: it cannot be rubber-stamped by regenerating."""
    report = validate(bundle.load(BUNDLES / name), today=GOLDEN_TODAY)
    assert report.errors == ()
    assert report.ok is True


def test_output_is_identical_across_runs():
    first = _fresh_golden_report()
    second = _fresh_golden_report()
    assert first.findings == second.findings


def test_output_does_not_depend_on_mapping_order():
    """Findings are sorted after collection, not merely appended."""
    loaded = bundle.load(NONCONFORMANT)
    items = list(loaded.concepts.items())
    random.Random(0).shuffle(items)
    shuffled = dataclasses.replace(loaded, concepts=dict(items))
    assert validate(shuffled, today=GOLDEN_TODAY).findings == _fresh_golden_report().findings


def test_one_parse_per_body(monkeypatch):
    """Asserted by counting markdown-it invocations, not by inspection."""
    _md.parse_body.cache_clear()
    calls: list[str] = []
    original = _md._MD.parse

    def counting(src, env=None):
        calls.append(src)
        return original(src) if env is None else original(src, env)

    monkeypatch.setattr(_md._MD, "parse", counting)
    loaded = bundle.load(NONCONFORMANT)
    validate(loaded, today=GOLDEN_TODAY)
    assert len(calls) == len(set(calls))


def test_tolerance_an_unknown_type_is_not_an_error(tmp_path):
    target = tmp_path / "a.md"
    write(target, "---\ntype: Playbook\ntitle: T\ndescription: D\n---\n\n# Definition\n")
    assert validate(bundle.load(tmp_path), today=GOLDEN_TODAY).ok is True


def test_tolerance_an_unknown_key_is_not_an_error(tmp_path):
    target = tmp_path / "a.md"
    write(target, '---\ntype: Metric\ntitle: T\ndescription: D\nnot: ["old definition"]\n---\n\n# D\n')
    assert validate(bundle.load(tmp_path), today=GOLDEN_TODAY).ok is True


def test_tolerance_a_missing_optional_family_is_not_an_error(tmp_path):
    target = tmp_path / "a.md"
    write(target, "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# Definition\n")
    report = validate(bundle.load(tmp_path), today=GOLDEN_TODAY)
    # Deliberately stricter than this test's siblings: a document with no
    # optional family present should produce literal silence, not merely no
    # error. If a future rule adds an advisory warning for this shape, that
    # is a signal to reconsider the rule, not to relax this assertion.
    assert report.findings == ()


def test_tolerance_a_broken_link_is_not_an_error(tmp_path):
    target = tmp_path / "a.md"
    write(target, "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n[x](./gone.md)\n")
    report = validate(bundle.load(tmp_path), today=GOLDEN_TODAY)
    assert report.ok is True
    assert report.by_code("links.broken") != ()


def test_a_malformed_concept_still_lets_every_other_one_validate(tmp_path):
    """The single most important behaviour in this child."""
    write(tmp_path / "broken.md", '---\ntype: "unterminated\n---\n\n# D\n')
    write(tmp_path / "fine.md", "---\ntitle: T\n---\n\n# Definition\n")
    report = validate(bundle.load(tmp_path), today=GOLDEN_TODAY)
    assert report.by_code("frontmatter.unparseable")[0].path == "broken.md"
    assert report.by_code("frontmatter.missing-type")[0].path == "fine.md"


def test_an_external_rule_sees_the_same_context_the_builtins_do(tmp_path):
    """The plugin contract, end to end, against a real bundle."""
    write(tmp_path / "a.md", "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# D\n")
    loaded = bundle.load(tmp_path)
    seen: list[RuleContext] = []

    def rule(ctx: RuleContext):
        seen.append(ctx)
        return ()

    validate(loaded, today=GOLDEN_TODAY, extra_rules=[rule])
    assert seen[0].bundle is loaded
    assert seen[0].today == GOLDEN_TODAY
    assert set(seen[0].links.bodies) == {"a"}
