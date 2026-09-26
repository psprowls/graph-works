from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf import about_rule
from code_wiki_okf.about import CODES
from okf_ext.schemas import AboutMandate
from okf_io import Finding, load_bundle, validate

TODAY = date(2026, 9, 25)

MANDATES = {
    "Adr": AboutMandate(entries="decisions"),
    "Explanation": AboutMandate(entries="claims"),
    "Reference": AboutMandate(),
    "HowTo": AboutMandate(),
}

_SCANNER = {
    "code-graph/demo/entities/packages/widgets.md": "type: Package\ntitle: W\nresource: pkg:acme/demo/widgets\n",
    "code-graph/demo/file-system/src/widgets.py.md": "type: File\ntitle: w\nresource: file:acme/demo/src/widgets.py\n",
    "code-graph/demo/entities/packages/twin.md": "type: Package\ntitle: T\nresource: pkg:acme/demo/twin\n",
    "code-graph/demo/entities/packages/twin-copy.md": "type: Package\ntitle: T2\nresource: pkg:acme/demo/twin\n",
    "sources/decoy.md": "type: Source\ntitle: D\ndescription: d\nresource: pkg:acme/demo/decoy\n",
}

_ADR = (
    "type: Adr\ntitle: A\ndescription: d\ndecision_date: 2026-01-01\n"
    "about: [pkg:acme/demo/widgets]\n"
    "decisions:\n  - id: D1\n    claim: Widgets are frozen.\n    constrains: [src/widgets.py]\n"
)
_EXPLANATION = (
    "type: Explanation\ntitle: E\ndescription: d\nabout: [pkg:acme/demo/widgets]\n"
    "claims:\n  - id: C1\n    claim: Widgets are frozen.\n    about: [file:acme/demo/src/widgets.py]\n"
)


def _write(root: Path, member: str, frontmatter: str) -> None:
    path = root / member
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n\n# Page\n", encoding="utf-8", newline="")


def _run(
    tmp_path: Path,
    pages: dict[str, str],
    *,
    repo_roots: tuple[Path, ...] = (),
    severity: str = "error",
    scope: frozenset[str] | None = None,
    mandates=MANDATES,
) -> list[Finding]:
    root = tmp_path / "bundle"
    for member, frontmatter in {**_SCANNER, **pages}.items():
        _write(root, member, frontmatter)
    report = validate(
        load_bundle(root),
        today=TODAY,
        extra_rules=[about_rule(mandates, repo_roots=repo_roots, severity=severity)],  # type: ignore[arg-type]
        scope=scope,
    )
    return [f for f in report.findings if f.code.split(".", 1)[0] in {"about", "claims"}]


def _codes(findings: list[Finding]) -> list[str]:
    return sorted(f.code for f in findings)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "widgets.py").write_text("", encoding="utf-8", newline="")
    return root


def test_codes_are_pinned() -> None:
    assert CODES == (
        "about.missing",
        "about.unresolved",
        "about.ambiguous",
        "claims.missing",
        "claims.about-unresolved",
        "claims.about-ambiguous",
        "claims.constrains-missing",
        "claims.duplicate-id",
    )


def test_a_conformant_bundle_is_silent(tmp_path: Path, repo: Path) -> None:
    pages = {"adrs/a.md": _ADR, "docs/explanations/e.md": _EXPLANATION}
    assert _run(tmp_path, pages, repo_roots=(repo,)) == []


def test_one_bundle_trips_every_code(tmp_path: Path, repo: Path) -> None:
    pages = {
        "docs/reference/no-about.md": "type: Reference\ntitle: R\ndescription: d\n",
        "docs/reference/unresolved.md": "type: Reference\ntitle: R\ndescription: d\nabout: [pkg:acme/demo/gone]\n",
        "docs/reference/ambiguous.md": "type: Reference\ntitle: R\ndescription: d\nabout: [pkg:acme/demo/twin]\n",
        "docs/explanations/no-claims.md": (
            "type: Explanation\ntitle: E\ndescription: d\nabout: [pkg:acme/demo/widgets]\n"
        ),
        "docs/explanations/bad-entries.md": (
            "type: Explanation\ntitle: E\ndescription: d\nabout: [pkg:acme/demo/widgets]\n"
            "claims:\n"
            "  - id: C1\n    claim: x\n    about: [pkg:acme/demo/gone]\n"
            "  - id: C2\n    claim: y\n    about: [pkg:acme/demo/twin]\n"
            "  - id: C1\n    claim: z\n    constrains: [src/missing.py]\n"
        ),
    }
    assert set(_codes(_run(tmp_path, pages, repo_roots=(repo,)))) == set(CODES)


def test_an_empty_about_list_is_missing(tmp_path: Path) -> None:
    pages = {"docs/reference/r.md": "type: Reference\ntitle: R\ndescription: d\nabout: []\n"}
    assert _codes(_run(tmp_path, pages)) == ["about.missing"]


def test_a_curated_page_with_a_colliding_resource_does_not_satisfy_about(tmp_path: Path) -> None:
    pages = {"docs/reference/r.md": "type: Reference\ntitle: R\ndescription: d\nabout: [pkg:acme/demo/decoy]\n"}
    assert _codes(_run(tmp_path, pages)) == ["about.unresolved"]


@pytest.mark.parametrize("status", ["draft", "deprecated", "superseded"])
def test_a_page_that_is_not_live_needs_no_entries(tmp_path: Path, status: str) -> None:
    pages = {
        "docs/explanations/e.md": (
            f"type: Explanation\ntitle: E\ndescription: d\nstatus: {status}\nabout: [pkg:acme/demo/widgets]\n"
        )
    }
    assert _run(tmp_path, pages) == []


@pytest.mark.parametrize("status_line", ["", "status: stable\n"])
def test_an_absent_or_stable_status_is_live(tmp_path: Path, status_line: str) -> None:
    pages = {
        "adrs/a.md": (
            "type: Adr\ntitle: A\ndescription: d\ndecision_date: 2026-01-01\n"
            f"{status_line}about: [pkg:acme/demo/widgets]\n"
        )
    }
    assert _codes(_run(tmp_path, pages)) == ["claims.missing"]


def test_a_mandate_without_entries_never_demands_claims(tmp_path: Path) -> None:
    pages = {"docs/how-tos/h.md": "type: HowTo\ntitle: H\ndescription: d\nabout: [pkg:acme/demo/widgets]\n"}
    assert _run(tmp_path, pages) == []


def test_reference_claims_are_still_checked_per_entry(tmp_path: Path) -> None:
    pages = {
        "docs/reference/r.md": (
            "type: Reference\ntitle: R\ndescription: d\nabout: [pkg:acme/demo/widgets]\n"
            "claims:\n  - id: C1\n    claim: x\n    about: [pkg:acme/demo/gone]\n"
        )
    }
    findings = _run(tmp_path, pages)
    assert _codes(findings) == ["claims.about-unresolved"]
    assert "`C1`" in findings[0].message


def test_an_unmandated_type_is_never_checked(tmp_path: Path) -> None:
    pages = {"docs/tutorials/t.md": "type: Tutorial\ntitle: T\ndescription: d\nabout: [pkg:acme/demo/gone]\n"}
    assert _run(tmp_path, pages) == []


@pytest.mark.parametrize(
    "frontmatter",
    [
        "about: pkg:acme/demo/widgets\n",
        "about: [7, {x: 1}]\n",
        "about: [pkg:acme/demo/widgets]\nclaims: C1\n",
        "about: [pkg:acme/demo/widgets]\nclaims: [just-a-string, 3]\n",
        "about: [pkg:acme/demo/widgets]\nclaims:\n  - id: 7\n    claim: x\n    about: gone\n    constrains: src/x\n",
        "about: [pkg:acme/demo/widgets]\nclaims:\n  - id: C1\n    claim: x\n    constrains: [7, ../escape, /abs]\n",
    ],
)
def test_malformed_values_are_skipped_not_reported(tmp_path: Path, repo: Path, frontmatter: str) -> None:
    """Reference: per-entry checks run over its `claims:`, but it has no
    `entries` mandate, so a missing list cannot mask a crash as `claims.missing`."""
    pages = {"docs/reference/r.md": "type: Reference\ntitle: R\ndescription: d\n" + frontmatter}
    assert _run(tmp_path, pages, repo_roots=(repo,)) == []


def test_a_page_that_fails_to_parse_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "docs/reference").mkdir(parents=True)
    (root / "docs/reference/r.md").write_text("---\ntype: Reference\n  bad: [\n---\n", encoding="utf-8", newline="")
    report = validate(load_bundle(root), today=TODAY, extra_rules=[about_rule(MANDATES)])
    assert [f for f in report.findings if f.code.startswith(("about.", "claims."))] == []


def test_scope_narrows_what_is_checked_not_what_resolves(tmp_path: Path) -> None:
    pages = {
        "docs/explanations/e.md": _EXPLANATION,
        "docs/reference/r.md": "type: Reference\ntitle: R\ndescription: d\n",
    }
    assert _run(tmp_path, pages, scope=frozenset({"docs/explanations/e.md"})) == []
    assert _codes(_run(tmp_path, pages, scope=frozenset({"docs/reference/r.md"}))) == ["about.missing"]


def test_constrains_are_skipped_with_no_repo_roots(tmp_path: Path) -> None:
    pages = {"adrs/a.md": _ADR.replace("src/widgets.py", "src/missing.py")}
    assert _run(tmp_path, pages) == []


def test_a_constrains_path_under_any_root_is_good(tmp_path: Path, repo: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    pages = {"adrs/a.md": _ADR}
    assert _run(tmp_path, pages, repo_roots=(other, repo)) == []
    assert _codes(_run(tmp_path, pages, repo_roots=(other,))) == ["claims.constrains-missing"]


def test_a_constrains_path_too_long_for_the_filesystem_is_reported_missing(tmp_path: Path, repo: Path) -> None:
    """A `constrains` segment past the OS name limit must not crash the rule --
    `Path.exists()` raises `OSError` for it on Python 3.12 -- so it is treated
    the same as any other target that resolves under no repository root."""
    pages = {"adrs/a.md": _ADR.replace("src/widgets.py", f"src/{'a' * 300}")}
    assert _codes(_run(tmp_path, pages, repo_roots=(repo,))) == ["claims.constrains-missing"]


def test_findings_carry_path_line_and_severity(tmp_path: Path) -> None:
    pages = {"docs/reference/r.md": "type: Reference\ntitle: R\ndescription: d\nabout: [pkg:acme/demo/gone]\n"}
    (finding,) = _run(tmp_path, pages, severity="warning")
    assert finding.path == "docs/reference/r.md"
    assert finding.severity == "warn"
    assert finding.line == 5
    assert "pkg:acme/demo/gone" in finding.message


def test_an_unknown_severity_raises() -> None:
    with pytest.raises(ValueError, match="severity"):
        about_rule(MANDATES, severity="fatal")  # type: ignore[arg-type]


def test_an_empty_mandate_map_finds_nothing(tmp_path: Path) -> None:
    pages = {"docs/reference/r.md": "type: Reference\ntitle: R\ndescription: d\n"}
    assert _run(tmp_path, pages, mandates={}) == []
