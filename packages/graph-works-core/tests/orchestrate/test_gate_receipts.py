"""Gate receipts: strict parsing, the field-by-field satisfaction rule, lookup."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.orchestrate import gate_receipts as gr

TREE = "a" * 40
RUN = gr.GateRun(
    run_id="20260928T120000Z-0a1b2c3d",
    repo="code",
    worktree="/w",
    head="b" * 40,
    tree=TREE,
    clean=True,
    tree_changed=False,
    scope="full",
    command="just check",
    names=(),
    exit=0,
    log_path="/w/.git/gw-gate/x/20260928T120000Z-0a1b2c3d.log",
    log_tail="ok",
    started="2026-09-28T12:00:00Z",
    duration_s=1.5,
)


def _write(root: Path, owner: str, runs, *, at: str | None = None) -> Path:
    target = root / (at or owner) / "references" / "03-gate-receipts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(gr.render_receipt(owner, runs, created="2026-09-28"), encoding="utf-8", newline="\n")
    return target


def test_render_then_parse_round_trips() -> None:
    owner, runs = gr.parse_gate_receipt(gr.render_receipt("work/a", [RUN], created="2026-09-28"))
    assert (owner, runs) == ("work/a", (RUN,))


def test_satisfies_the_exact_request() -> None:
    assert gr.satisfies(RUN, repo="code", tree=TREE, command="just check")


@pytest.mark.parametrize(
    "change",
    [
        {"repo": "other"},
        {"tree": "c" * 40},
        {"clean": False},
        {"tree_changed": True},
        {"exit": 1},
        {"scope": "scoped"},
        {"command": "just check-old"},
    ],
)
def test_each_field_flip_fails_to_satisfy(change) -> None:
    assert not gr.satisfies(replace(RUN, **change), repo="code", tree=TREE, command="just check")


def test_a_changed_configured_command_invalidates_older_receipts() -> None:
    assert not gr.satisfies(RUN, repo="code", tree=TREE, command="just check --all")


def test_lookup_finds_another_items_receipt(tmp_path: Path) -> None:
    _write(tmp_path, "work/epic/children/child", [RUN])
    lookup = gr.find_satisfying(tmp_path, repo="code", tree=TREE, command="just check")
    assert lookup.match == gr.GateMatch("work/epic/children/child", RUN)
    assert lookup.warnings == ()


def test_lookup_finds_an_archived_items_receipt(tmp_path: Path) -> None:
    _write(tmp_path, "work/feature-old", [RUN], at="work/_archive/feature-old")
    lookup = gr.find_satisfying(tmp_path, repo="code", tree=TREE, command="just check")
    assert lookup.match is not None and lookup.match.owner == "work/feature-old"


def test_lookup_prefers_the_newest_satisfying_run(tmp_path: Path) -> None:
    newer = replace(RUN, run_id="20260928T130000Z-00000000", started="2026-09-28T13:00:00Z")
    _write(tmp_path, "work/a", [RUN])
    _write(tmp_path, "work/b", [newer])
    lookup = gr.find_satisfying(tmp_path, repo="code", tree=TREE, command="just check")
    assert lookup.match is not None and lookup.match.run == newer


def test_malformed_receipt_warns_and_contributes_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "work/a/references/03-gate-receipts.md"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "---\ntype: Explanation\nreceipt_version: 1\nowner: work/a\nruns: [{run_id: 1}]\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    _write(tmp_path, "work/b", [replace(RUN, exit=1)])
    lookup = gr.find_satisfying(tmp_path, repo="code", tree=TREE, command="just check")
    assert lookup.match is None
    assert len(lookup.warnings) == 1 and "work/a/references/03-gate-receipts.md" in lookup.warnings[0]


def test_non_utf8_receipt_warns(tmp_path: Path) -> None:
    bad = tmp_path / "work/a/references/03-gate-receipts.md"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff\xfe---\n")
    assert len(gr.find_satisfying(tmp_path, repo="code", tree=TREE, command="x").warnings) == 1


def test_log_tail_with_control_bytes_round_trips() -> None:
    raw = "".join(f"line {i}\n" for i in range(60)) + "\x1b[31mred\x1b[0m\r\nbell\x07 done"
    tail = gr.sanitize_tail(raw)
    assert tail.splitlines()[-2:] == ["red", "bell done"]
    assert len(tail.splitlines()) == 40
    run = replace(RUN, log_tail=tail)
    assert gr.parse_gate_receipt(gr.render_receipt("work/a", [run], created="2026-09-28"))[1] == (run,)


def test_empty_tail_is_empty() -> None:
    assert gr.sanitize_tail("") == ""


def _mutated(**change: object) -> str:
    data = gr.run_data(RUN)
    data.update(change)
    page = gr.parse(gr.render_receipt("work/a", [], created="2026-09-28"))
    page.set("runs", [data])
    return page.serialize()


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": 1},
        {"head": "nothex"},
        {"tree": 5},
        {"clean": "yes"},
        {"tree_changed": 1},
        {"scope": "partial"},
        {"exit": True},
        {"exit": "0"},
        {"duration_s": True},
        {"duration_s": "1"},
        {"names": "a"},
        {"names": [1]},
    ],
)
def test_strict_parser_rejects_each_malformed_field(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(_mutated(**change))


def test_strict_parser_rejects_a_non_mapping_run_and_bad_header() -> None:
    page = gr.parse(gr.render_receipt("work/a", [], created="2026-09-28"))
    page.set("runs", ["nope"])
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(page.serialize())
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(
            gr.render_receipt("work/a", [], created="2026-09-28").replace("receipt_version: 1", "receipt_version: 2")
        )


def test_any_for_tree_sees_red_receipts_but_not_other_trees(tmp_path: Path) -> None:
    _write(tmp_path, "work/a", [replace(RUN, exit=1)])
    assert gr.any_for_tree(tmp_path, repo="code", tree=TREE)
    assert not gr.any_for_tree(tmp_path, repo="code", tree="c" * 40)
    assert not gr.any_for_tree(tmp_path, repo="other", tree=TREE)
    (tmp_path / "work/b/references").mkdir(parents=True)
    (tmp_path / "work/b/references/03-gate-receipts.md").write_bytes(b"\xff")
    assert gr.any_for_tree(tmp_path, repo="code", tree=TREE)
