"""Acceptance tests for `scripts/seed_tag_vocabulary.py`.

Outside the repo's coverage `source` list on purpose: `scripts/` is repo
tooling, not a package, so these do not move the 95% gate. They ARE inside
`testpaths`, so `just test` runs them. Run alone with
`uv run pytest scripts/tests/test_seed_tag_vocabulary.py`.

Every fixture here is a synthetic mini-vault, not a copy of the real one: the
real vault does not exist until C4 resolves, and a fixture that tracked it
would have to be re-vendored on every migration phase.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from okf_ext import tags as ext_tags
from seed_tag_vocabulary import (
    QUARANTINE,
    Floor,
    Row,
    SeedError,
    coverage,
    gate_failures,
    is_live,
    load,
    render_evidence,
    resolve_ignore,
    rows,
    run_rename,
    run_verify,
)


def page(vault: Path, path: str, *, tags: list[str] | None = None, extra: str = "") -> Path:
    """One frontmatter-carrying page. `tags=None` writes no `tags:` key."""
    target = vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "title: " + target.stem]
    if tags is not None:
        lines.append("tags:")
        lines.extend(f"  - {tag}" for tag in tags)
    if extra:
        lines.append(extra)
    lines += ["---", "", f"# {target.stem}", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def workspace(tmp_path: Path, *, ignore: list[str] | None = None, manifest: bool = True) -> Path:
    """A workspace: `wiki/` bundle root, `.gw/` declarations, `workspace.yaml`."""
    root = tmp_path / "graph-works"
    (root / "wiki").mkdir(parents=True)
    (root / ".gw").mkdir()
    (root / "wiki" / "index.md").write_text("---\ntitle: Index\n---\n\n# Index\n", encoding="utf-8")
    (root / "wiki" / "log.md").write_text("---\ntitle: Log\n---\n\n# Log\n", encoding="utf-8")
    if manifest:
        body = "topic: test\n"
        if ignore is None:
            pass
        elif ignore:
            body += "ignore:\n" + "".join(f'  - "{pattern}"\n' for pattern in ignore)
        else:
            body += "ignore: []\n"
        (root / "workspace.yaml").write_text(body, encoding="utf-8")
    return root


# --- the ignore set comes from the workspace, never from a literal ----------


def test_ignore_is_read_from_the_manifest(tmp_path: Path) -> None:
    root = workspace(tmp_path, ignore=["entities/**", "guidance/**"])
    assert resolve_ignore(root) == ("entities/**", "guidance/**")


def test_absent_ignore_key_is_the_empty_set(tmp_path: Path) -> None:
    assert resolve_ignore(workspace(tmp_path)) == ()


def test_explicit_override_does_not_read_the_manifest(tmp_path: Path) -> None:
    root = workspace(tmp_path, manifest=False)
    assert resolve_ignore(root, ["a/*"]) == ("a/*",)


def test_missing_manifest_names_the_override_flag(tmp_path: Path) -> None:
    with pytest.raises(SeedError, match="--ignore"):
        resolve_ignore(workspace(tmp_path, manifest=False))


def test_non_list_ignore_is_refused(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    (root / "workspace.yaml").write_text("ignore: entities/**\n", encoding="utf-8")
    with pytest.raises(SeedError, match="list of strings"):
        resolve_ignore(root)


# --- a partial corpus is not a corpus a policy may be decided over ----------


def test_a_skipped_page_halts_the_run(tmp_path: Path) -> None:
    """`tags:` as a mapping is `tags-not-a-sequence` — the inventory skips it."""
    root = workspace(tmp_path)
    page(root / "wiki", "concepts/good.md", tags=["okf"])
    (root / "wiki" / "concepts" / "bad.md").write_text(
        "---\ntitle: bad\ntags:\n  a: b\n---\n\n# bad\n", encoding="utf-8"
    )
    with pytest.raises(SeedError, match=r"concepts/bad\.md"):
        load(root, ())


def test_a_clean_corpus_loads(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    page(root / "wiki", "concepts/good.md", tags=["okf", "core"])
    bundle, inv = load(root, ())
    assert inv.counts == {"core": 1, "okf": 1}
    assert bundle.root == root / "wiki"


def test_the_quarantine_is_derived_from_the_package(tmp_path: Path) -> None:
    from work_tracker_okf.vocabulary import CONTRIBUTED_TAGS

    assert frozenset(definition.name for definition in CONTRIBUTED_TAGS) == QUARANTINE
    assert frozenset({"perf", "security"}) == QUARANTINE


def build(tmp_path: Path, pages: dict[str, list[str]]) -> tuple[object, object]:
    root = workspace(tmp_path)
    for path, tag_list in pages.items():
        page(root / "wiki", path, tags=tag_list)
    return load(root, ())


def pack(tmp_path: Path, pages: dict[str, list[str]]) -> str:
    _, inv = build(tmp_path, pages)
    return render_evidence(inv, as_of="2026-09-01")


# --- the live column is the one piece of judgment the pack carries ----------


def test_archive_only_tag_is_not_live() -> None:
    assert is_live(("work/_archive/old",)) is False
    assert is_live(("work/epic/children/_archive/old",)) is False


def test_one_live_carrier_makes_the_tag_live() -> None:
    assert is_live(("work/_archive/old", "adrs/0001-x")) is True


def test_evidence_live_column(tmp_path: Path) -> None:
    text = pack(
        tmp_path,
        {
            "work/_archive/old.md": ["rebase"],
            "adrs/0001-x.md": ["rebase", "okf"],
        },
    )
    assert "| rebase |" in text
    lines = [line for line in text.splitlines() if line.startswith("| rebase |")]
    assert lines and lines[0].endswith("| yes |")
    text = pack(tmp_path / "second", {"work/_archive/old.md": ["rebase"]})
    lines = [line for line in text.splitlines() if line.startswith("| rebase |")]
    assert lines and lines[0].endswith("| no |")


# --- the quarantine is excluded from every count ---------------------------


def test_evidence_quarantine_does_not_move_the_arithmetic(tmp_path: Path) -> None:
    without = pack(tmp_path / "a", {"adrs/0001-x.md": ["okf", "core"], "adrs/0002-y.md": ["okf"]})
    with_pkg = pack(
        tmp_path / "b",
        {"adrs/0001-x.md": ["okf", "core", "perf"], "adrs/0002-y.md": ["okf", "security"]},
    )
    head = lambda text: text.split("## Quarantine")[0]  # noqa: E731
    assert head(without) == head(with_pkg)
    assert "perf" in with_pkg.split("## Quarantine")[1]
    assert "perf" not in head(with_pkg)


# --- determinism -----------------------------------------------------------


def test_evidence_is_byte_identical_across_runs(tmp_path: Path) -> None:
    pages = {"adrs/0001-x.md": ["okf", "core"], "concepts/z.md": ["core", "plugin", "plugins"]}
    _, inv = build(tmp_path, pages)
    assert render_evidence(inv, as_of="2026-09-01") == render_evidence(inv, as_of="2026-09-01")


# --- sections --------------------------------------------------------------


def test_evidence_has_six_sections_in_order(tmp_path: Path) -> None:
    text = pack(tmp_path, {"adrs/0001-x.md": ["okf"]})
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Totals",
        "## Frequency",
        "## Coverage",
        "## Similarity",
        "## Normalization",
        "## Quarantine",
    ]


def test_evidence_normalization_is_none_on_a_canonical_corpus(tmp_path: Path) -> None:
    text = pack(tmp_path, {"adrs/0001-x.md": ["okf", "core"]})
    section = text.split("## Normalization")[1].split("## Quarantine")[0]
    assert "none" in section


def test_evidence_normalization_reports_a_non_canonical_tag(tmp_path: Path) -> None:
    text = pack(tmp_path, {"adrs/0001-x.md": ["Data Quality"]})
    section = text.split("## Normalization")[1].split("## Quarantine")[0]
    assert "data-quality" in section
    assert "none" not in section


def test_evidence_similarity_runs_both_cutoffs(tmp_path: Path) -> None:
    text = pack(tmp_path, {"adrs/0001-x.md": ["plugin", "plugins"], "concepts/z.md": ["plugin"]})
    section = text.split("## Similarity")[1].split("## Normalization")[0]
    assert "cutoff 0.8" in section
    assert "cutoff 0.75" in section
    assert "plugin" in section


# --- the coverage curve ----------------------------------------------------


def test_coverage_counts_admitted_and_covered() -> None:
    sample = (
        Row(tag="a", count=3, lanes=("adrs",), live=True),
        Row(tag="b", count=1, lanes=("adrs",), live=True),
    )
    curve = coverage(sample)
    assert curve[0] == Floor(n=1, admitted=2, covered=4, share=1.0)
    assert curve[1] == Floor(n=2, admitted=1, covered=3, share=0.75)


def test_coverage_of_an_empty_corpus_does_not_divide_by_zero() -> None:
    assert coverage(()) == tuple(Floor(n=n, admitted=0, covered=0, share=0.0) for n in (1, 2, 3, 4, 5, 6))


def test_rows_are_sorted_by_count_then_name(tmp_path: Path) -> None:
    _, inv = build(tmp_path, {"adrs/0001-x.md": ["b", "a"], "adrs/0002-y.md": ["a"]})
    assert [row.tag for row in rows(inv)] == ["a", "b"]


GOOD = """version: 1
tags:
  - name: okf
    description: The Open Knowledge Format itself.
  - name: plugin
    description: A Claude Code plugin.
  - name: plugins
    description: Superseded spelling of `plugin`.
    deprecated: true
    replaced_by: plugin
  - name: perf
    description: A defect whose impact is performance.
  - name: security
    description: A defect with a security impact.
"""


def vocab_file(tmp_path: Path, text: str, *, bom: bool = False) -> Path:
    target = tmp_path / "tags.yaml"
    target.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return target


def test_gate_passes_a_well_formed_seed(tmp_path: Path) -> None:
    assert gate_failures(vocab_file(tmp_path, GOOD)) == []


# --- the two bug-shaped constraints, asserted rather than depended on ------


def test_gate_refuses_a_bom(tmp_path: Path) -> None:
    failures = gate_failures(vocab_file(tmp_path, GOOD, bom=True))
    assert any("BOM" in failure for failure in failures)


def test_gate_refuses_two_column_zero_tags_keys(tmp_path: Path) -> None:
    failures = gate_failures(vocab_file(tmp_path, GOOD + "tags:\n  - name: x\n"))
    assert any("`tags:`" in failure for failure in failures)


# --- every loader refusal is a gate failure, not a traceback ---------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(GOOD.replace("    deprecated: true", '    deprecated: "false"'), id="deprecated-string"),
        pytest.param(
            GOOD.replace("    description: A Claude Code plugin.", "    description: x\n    replaced_by: okf"),
            id="replaced-by-on-live",
        ),
        pytest.param(GOOD.replace("    replaced_by: plugin", "    replaced_by: plugins"), id="chain"),
        pytest.param(GOOD + "  - name: okf\n    description: twice.\n", id="duplicate-name"),
        pytest.param(GOOD.replace("    replaced_by: plugin", "    replace_by: plugin"), id="unknown-key"),
        pytest.param(GOOD.replace("version: 1\n", ""), id="missing-version"),
        pytest.param(GOOD.replace("version: 1", "version: '1'"), id="non-integer-version"),
    ],
)
def test_gate_surfaces_every_loader_refusal(tmp_path: Path, text: str) -> None:
    failures = gate_failures(vocab_file(tmp_path, text))
    assert any("load_vocabulary" in failure for failure in failures)


# --- the ADR-0029 seam ----------------------------------------------------


def test_gate_refuses_a_missing_quarantine_entry(tmp_path: Path) -> None:
    text = GOOD.replace("  - name: perf\n    description: A defect whose impact is performance.\n", "")
    failures = gate_failures(vocab_file(tmp_path, text))
    assert any("perf" in failure for failure in failures)


def test_gate_refuses_an_edited_quarantine_instruction(tmp_path: Path) -> None:
    text = GOOD.replace(
        "  - name: perf\n    description: A defect whose impact is performance.\n",
        "  - name: perf\n    description: A defect whose impact is performance.\n    deprecated: true\n",
    )
    failures = gate_failures(vocab_file(tmp_path, text))
    assert any("perf" in failure and "deprecated" in failure for failure in failures)


def test_gate_allows_a_reworded_quarantine_description(tmp_path: Path) -> None:
    """A differing description is `TagDrift` — reported by the package route,
    never overwritten. The vault may reword freely."""
    text = GOOD.replace("    description: A defect whose impact is performance.", "    description: Slow.")
    assert gate_failures(vocab_file(tmp_path, text)) == []


MERGE = """version: 1
tags:
  - name: plugin
    description: A Claude Code plugin.
  - name: plugins
    description: Superseded spelling of `plugin`.
    deprecated: true
    replaced_by: plugin
  - name: perf
    description: A defect whose impact is performance.
  - name: security
    description: A defect with a security impact.
"""


def seeded(tmp_path: Path, pages: dict[str, list[str]], text: str = MERGE) -> Path:
    root = workspace(tmp_path)
    for path, tag_list in pages.items():
        page(root / "wiki", path, tags=tag_list)
    (root / ".gw" / "tags.yaml").write_text(text, encoding="utf-8")
    return root


def test_rename_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = seeded(tmp_path, {"adrs/0001-x.md": ["plugins"]})
    before = (root / "wiki" / "adrs" / "0001-x.md").read_bytes()
    assert run_rename(root, (), write=False) == 0
    assert (root / "wiki" / "adrs" / "0001-x.md").read_bytes() == before


def test_rename_applies_the_deprecation(tmp_path: Path) -> None:
    root = seeded(tmp_path, {"adrs/0001-x.md": ["plugins"], "concepts/z.md": ["plugin"]})
    assert run_rename(root, (), write=True) == 0
    _, inv = load(root, ())
    assert "plugins" not in inv.counts
    assert inv.counts["plugin"] == 2


def test_rename_collapses_a_duplicate_rather_than_double_counting(tmp_path: Path) -> None:
    """A page carrying both spellings ends with the survivor exactly once."""
    root = seeded(tmp_path, {"adrs/0001-x.md": ["plugin", "plugins"]})
    assert run_rename(root, (), write=True) == 0
    _, inv = load(root, ())
    assert inv.counts == {"plugin": 1}
    body = (root / "wiki" / "adrs" / "0001-x.md").read_text(encoding="utf-8")
    assert body.count("plugin") == 1


def test_rename_plans_nothing_for_a_deprecation_without_a_replacement(tmp_path: Path) -> None:
    text = MERGE.replace("    replaced_by: plugin\n", "")
    root = seeded(tmp_path, {"adrs/0001-x.md": ["plugins"]}, text=text)
    bundle, _ = load(root, ())
    vocab = ext_tags.load_vocabulary(root / ".gw" / "tags.yaml")
    assert ext_tags.plan_from_vocabulary(bundle, vocab).is_empty


SEED = """version: 1
tags:
  - name: plugin
    description: A Claude Code plugin.
  - name: perf
    description: A defect whose impact is performance.
  - name: security
    description: A defect with a security impact.
"""


def test_verify_passes_a_clean_seed(tmp_path: Path) -> None:
    root = seeded(tmp_path, {"adrs/0001-x.md": ["plugin"]}, text=SEED)
    assert run_verify(root, (), today=date(2026, 9, 1)) == 0


def test_verify_refuses_a_non_canonical_corpus(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = seeded(tmp_path, {"adrs/0001-x.md": ["Plugin"]}, text=SEED)
    assert run_verify(root, (), today=date(2026, 9, 1)) == 1
    assert "Plugin" in capsys.readouterr().err


def test_verify_reports_a_surviving_deprecated_spelling(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = seeded(tmp_path, {"adrs/0001-x.md": ["plugins"]}, text=MERGE)
    assert run_verify(root, (), today=date(2026, 9, 1)) == 1
    assert "plugins" in capsys.readouterr().err


def test_verify_splits_unknown_findings_by_lane(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The vocabulary is invisible to the work lane — `rule_set` omits it."""
    root = seeded(tmp_path, {"adrs/0001-x.md": ["stray"], "work/w.md": ["stray"]}, text=SEED)
    run_verify(root, (), today=date(2026, 9, 1))
    out = capsys.readouterr().out
    assert "enforced" in out
    assert "unenforced" in out
