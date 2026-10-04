"""Gate receipts: strict parsing, the field-by-field satisfaction rule, lookup."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.orchestrate import gate_receipts as gr
from graph_works_core.workspace.layout import WorkspaceLayout

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


def _layout(root: Path) -> WorkspaceLayout:
    return WorkspaceLayout(
        root=root,
        config_dir=root / ".gw",
        cache_dir=root / ".gw/cache",
        bundle_dir=root,
        worktrees_dir=root / ".gw/worktrees",
    )


def _write(root: Path, owner: str, runs, *, at: str | None = None) -> Path:
    target = root / (at or owner) / "references" / "03-gate-receipts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(gr.render_receipt(owner, runs, created="2026-09-28"), encoding="utf-8", newline="\n")
    return target


def test_render_then_parse_round_trips() -> None:
    owner, runs = gr.parse_gate_receipt(gr.render_receipt("work/a", [RUN], created="2026-09-28"))
    assert (owner, runs) == ("work/a", (RUN,))


H_LIB, H_APP = "1" * 64, "2" * 64
HASHES = {"lib": H_LIB, "app": H_APP}


def v2(
    run_id: str,
    started: str,
    *,
    units=(),
    repo_wide=None,
    exit=0,
    clean=True,
    tree_changed=False,
    tree=TREE,
    scope="full",
    command="just check",
):
    return replace(
        RUN,
        run_id=run_id,
        started=started,
        exit=exit,
        clean=clean,
        tree_changed=tree_changed,
        tree=tree,
        scope=scope,
        command=command,
        manifest_hash="d" * 64,
        units=tuple(units),
        repo_wide=repo_wide,
    )


def green(name: str, h: str) -> gr.UnitEntry:
    return gr.UnitEntry(name, h, ran=True, exit=0, log_path="/l", duration_s=1.0)


def wide(*, exit=0, tree=TREE, command="just gate-repo-wide") -> gr.RepoWideEntry:
    return gr.RepoWideEntry(tree=tree, command=command, ran=True, exit=exit, duration_s=1.0)


def put(root: Path, owner: str, *runs: gr.GateRun) -> None:
    target = root / owner / "references" / "03-gate-receipts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(gr.render_receipt(owner, list(runs), created="2026-09-28"), encoding="utf-8", newline="\n")


def ev(root: Path, **kw):
    args = dict(
        repo="code", tree=TREE, full_command="just check", repo_wide_command="just gate-repo-wide", hashes=HASHES
    )
    args.update(kw)
    return gr.evaluate(_layout(root), **args)


def test_units_from_item_a_and_repo_wide_from_item_b_satisfy(tmp_path: Path) -> None:
    put(
        tmp_path,
        "work/a",
        v2(
            "20260928T120001Z-00000001",
            "2026-09-28T12:00:01Z",
            units=[green("lib", H_LIB), green("app", H_APP)],
            tree="c" * 40,
        ),
    )
    put(tmp_path, "work/b", v2("20260928T120002Z-00000002", "2026-09-28T12:00:02Z", repo_wide=wide()))
    result = ev(tmp_path)
    assert result.satisfied and result.stale == () and result.repo_wide_green
    assert result.evidence is not None and result.evidence.owner == "work/b"
    assert {(u.name, u.owner) for u in result.evidence.units} == {("lib", "work/a"), ("app", "work/a")}


def test_a_unit_entry_from_a_red_run_still_counts(tmp_path: Path) -> None:
    put(
        tmp_path,
        "work/a",
        v2(
            "20260928T120001Z-00000001",
            "2026-09-28T12:00:01Z",
            exit=1,
            repo_wide=wide(),
            units=[green("lib", H_LIB), replace(green("app", H_APP), exit=1)],
        ),
    )
    result = ev(tmp_path)
    assert not result.satisfied and result.stale == ("app",) and "lib" in result.green


def test_reused_entries_never_count(tmp_path: Path) -> None:
    reused = gr.UnitEntry("lib", H_LIB, ran=False, reused_from=gr.Reuse("work/x", "r"))
    put(
        tmp_path,
        "work/a",
        v2("20260928T120001Z-00000001", "2026-09-28T12:00:01Z", repo_wide=wide(), units=[reused, green("app", H_APP)]),
    )
    assert ev(tmp_path).stale == ("lib",)


@pytest.mark.parametrize("taint", [{"clean": False}, {"tree_changed": True}])
def test_dirty_or_changed_runs_never_count(tmp_path: Path, taint: dict[str, bool]) -> None:
    put(
        tmp_path,
        "work/a",
        v2(
            "20260928T120001Z-00000001",
            "2026-09-28T12:00:01Z",
            repo_wide=wide(),
            units=[green("lib", H_LIB), green("app", H_APP)],
            **taint,
        ),
    )
    result = ev(tmp_path)
    assert not result.satisfied and set(result.stale) == {"lib", "app"} and not result.repo_wide_green


def test_repo_wide_must_match_tree_and_command(tmp_path: Path) -> None:
    units = [green("lib", H_LIB), green("app", H_APP)]
    put(
        tmp_path,
        "work/a",
        v2("20260928T120001Z-00000001", "2026-09-28T12:00:01Z", units=units, repo_wide=wide(tree="c" * 40)),
    )
    put(tmp_path, "work/b", v2("20260928T120002Z-00000002", "2026-09-28T12:00:02Z", repo_wide=wide(command="other")))
    result = ev(tmp_path)
    assert not result.satisfied and result.stale == () and not result.repo_wide_green


def test_no_repo_wide_step_needs_only_units(tmp_path: Path) -> None:
    put(
        tmp_path,
        "work/a",
        v2("20260928T120001Z-00000001", "2026-09-28T12:00:01Z", units=[green("lib", H_LIB), green("app", H_APP)]),
    )
    result = ev(tmp_path, repo_wide_command=None)
    assert result.satisfied and result.evidence is not None and result.evidence.run_id == "20260928T120001Z-00000001"


def test_v1_full_green_run_on_the_tree_satisfies_everything(tmp_path: Path) -> None:
    put(tmp_path, "work/old", RUN)  # v1-shaped: no units, scope full, command "just check", exit 0
    result = ev(tmp_path)
    assert result.satisfied and result.evidence is not None and result.evidence.owner == "work/old"
    assert {u.name for u in result.evidence.units} == {"lib", "app"}


@pytest.mark.parametrize(
    "change", [{"scope": "scoped"}, {"command": "just check --all"}, {"exit": 1}, {"tree": "c" * 40}]
)
def test_v1_run_failing_todays_rule_does_not_satisfy(tmp_path: Path, change: dict[str, object]) -> None:
    put(tmp_path, "work/old", replace(RUN, **change))
    assert not ev(tmp_path).satisfied


def test_newest_green_wins_and_malformed_file_is_a_warning(tmp_path: Path) -> None:
    put(
        tmp_path,
        "work/a",
        v2(
            "20260928T120001Z-00000001",
            "2026-09-28T12:00:01Z",
            repo_wide=wide(),
            units=[green("lib", H_LIB), green("app", H_APP)],
        ),
    )
    put(tmp_path, "work/b", v2("20260928T120005Z-00000005", "2026-09-28T12:00:05Z", units=[green("lib", H_LIB)]))
    bad = tmp_path / "work/c/references/03-gate-receipts.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("not a receipt", encoding="utf-8", newline="\n")
    result = ev(tmp_path)
    assert result.green["lib"].owner == "work/b" and len(result.warnings) == 1


@pytest.mark.parametrize("change", [{"repo": "other"}, {"clean": False}, {"tree_changed": True}])
def test_v1_tainted_or_foreign_runs_do_not_satisfy(tmp_path: Path, change) -> None:
    put(tmp_path, "work/old", replace(RUN, **change))
    assert not ev(tmp_path).satisfied


def test_empty_v2_record_cannot_use_v1_compatibility(tmp_path: Path) -> None:
    put(tmp_path, "work/a", v2("20260928T120001Z-00000001", "2026-09-28T12:00:01Z"))
    result = ev(tmp_path)
    assert not result.satisfied and result.stale == ("app", "lib")


def test_repo_wide_entry_must_match_its_containing_run_tree(tmp_path: Path) -> None:
    put(
        tmp_path,
        "work/a",
        v2(
            "20260928T120001Z-00000001",
            "2026-09-28T12:00:01Z",
            units=[green("lib", H_LIB), green("app", H_APP)],
            repo_wide=wide(),
            tree="c" * 40,
        ),
    )
    result = ev(tmp_path)
    assert not result.satisfied and result.stale == () and not result.repo_wide_green


def test_implicit_scoped_command_hash_does_not_satisfy_full_gate(tmp_path: Path) -> None:
    from graph_works_core.orchestrate import gate_units

    put(
        tmp_path,
        "work/a",
        v2(
            "20260928T120001Z-00000001",
            "2026-09-28T12:00:01Z",
            units=[green("tree", gate_units.tree_hash(TREE, "just check-pkg lib"))],
            scope="scoped",
            command="just check-pkg lib",
        ),
    )
    result = ev(tmp_path, repo_wide_command=None, hashes={"tree": gate_units.tree_hash(TREE, "just check")})
    assert not result.satisfied and result.stale == ("tree",)


def test_lookup_finds_an_archived_items_receipt(tmp_path: Path) -> None:
    _write(tmp_path, "work/feature-old", [RUN], at="work/_archive/feature-old")
    result = ev(tmp_path)
    assert result.evidence is not None and result.evidence.owner == "work/feature-old"


def test_non_utf8_receipt_warns(tmp_path: Path) -> None:
    bad = tmp_path / "work/a/references/03-gate-receipts.md"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff\xfe---\n")
    assert len(ev(tmp_path).warnings) == 1


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
            gr.render_receipt("work/a", [], created="2026-09-28").replace("receipt_version: 2", "receipt_version: 3")
        )


def test_any_for_tree_sees_red_receipts_but_not_other_trees(tmp_path: Path) -> None:
    _write(tmp_path, "work/a", [replace(RUN, exit=1)])
    assert gr.any_for_tree(_layout(tmp_path), repo="code", tree=TREE)
    assert not gr.any_for_tree(_layout(tmp_path), repo="code", tree="c" * 40)
    assert not gr.any_for_tree(_layout(tmp_path), repo="other", tree=TREE)
    (tmp_path / "work/b/references").mkdir(parents=True)
    (tmp_path / "work/b/references/03-gate-receipts.md").write_bytes(b"\xff")
    assert gr.any_for_tree(_layout(tmp_path), repo="code", tree=TREE)


UNITS_RUN = replace(
    RUN,
    run_id="20260928T120001Z-0000000a",
    manifest_hash="d" * 64,
    repo_wide=gr.RepoWideEntry(TREE, "just gate-repo-wide", ran=True, exit=0, duration_s=4.0),
    units=(
        gr.UnitEntry("lib", "1" * 64, ran=True, exit=0, log_path="/l/lib.log", duration_s=2.0),
        gr.UnitEntry("app", "2" * 64, ran=False, reused_from=gr.Reuse("work/other", "r")),
    ),
)


def test_v2_run_round_trips() -> None:
    text = gr.render_receipt("work/a", [RUN, UNITS_RUN], created="2026-09-28")
    assert "receipt_version: 2" in text
    assert gr.parse_gate_receipt(text) == ("work/a", (RUN, UNITS_RUN))


def test_v1_page_still_parses() -> None:
    text = gr.render_receipt("work/a", [RUN], created="2026-09-28").replace("receipt_version: 2", "receipt_version: 1")
    assert gr.parse_gate_receipt(text)[1] == (RUN,)


def test_entry_optional_fields_round_trip() -> None:
    run = replace(
        RUN,
        repo_wide=gr.RepoWideEntry(TREE, "checks", True, exit=0),
        units=(
            gr.UnitEntry("lib", "1" * 64, True, exit=0),
            gr.UnitEntry(
                "app", "2" * 64, False, exit=0, log_path="/l", duration_s=0.0, reused_from=gr.Reuse("work/a", "r")
            ),
        ),
    )
    assert gr.parse_gate_receipt(gr.render_receipt("work/a", [run], created="2026-09-28"))[1] == (run,)


@pytest.mark.parametrize(
    "change",
    [
        {"hash": "short"},
        {"name": " "},
        {"ran": "yes"},
        {"exit": None},
        {"exit": True},
        {"duration_s": True},
        {"duration_s": -1},
        {"duration_s": float("inf")},
        {"duration_s": float("nan")},
        {"log_path": 1},
        {"ran": False, "reused_from": None},
        {"reused_from": "no"},
        {"reused_from": {"owner": "", "run_id": "r"}},
        {"reused_from": {"owner": "work/a", "run_id": 1}},
        {"reused_from": {"owner": "work/a", "run_id": " "}},
    ],
)
def test_malformed_unit_entry_makes_the_file_malformed(change: dict[str, object]) -> None:
    entry: dict[str, object] = {"name": "lib", "hash": "1" * 64, "ran": True, "exit": 0}
    entry.update(change)
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(_mutated(units=[entry]))


@pytest.mark.parametrize(
    "change",
    [
        {"tree": "short"},
        {"command": " "},
        {"ran": 1},
        {"exit": True},
        {"exit": None},
        {"duration_s": -1},
        {"duration_s": float("inf")},
        {"duration_s": float("nan")},
        {"ran": False, "reused_from": None},
        {"reused_from": {"owner": "", "run_id": "r"}},
    ],
)
def test_malformed_repo_wide_entry_makes_the_file_malformed(change: dict[str, object]) -> None:
    entry: dict[str, object] = {"tree": TREE, "command": "checks", "ran": True, "exit": 0}
    entry.update(change)
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(_mutated(repo_wide=entry))


@pytest.mark.parametrize(
    "change",
    [
        {"manifest_hash": "short"},
        {"units": {}},
        {"units": ["bad"]},
        {"repo_wide": "bad"},
        {"duration_s": -1},
        {"duration_s": float("inf")},
        {"duration_s": float("nan")},
    ],
)
def test_malformed_v2_run_fields_are_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(_mutated(**change))


@pytest.mark.parametrize("version", [True, False, 1.0, 2.0, "2", 3])
def test_receipt_version_is_a_supported_integer(version: object) -> None:
    page = gr.parse(gr.render_receipt("work/a", [], created="2026-09-28"))
    page.set("receipt_version", version)
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(page.serialize())


@pytest.mark.parametrize("field", ["run_id", "repo", "worktree", "command", "log_path", "started"])
def test_required_run_strings_are_nonblank(field: str) -> None:
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(_mutated(**{field: " "}))


def test_owner_is_nonblank() -> None:
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(gr.render_receipt(" ", [RUN], created="2026-09-28"))


@pytest.mark.parametrize(
    "change", [{"exit": True}, {"duration_s": False}, {"duration_s": -1}, {"duration_s": float("inf")}]
)
@pytest.mark.parametrize("kind", ["units", "repo_wide"])
def test_reused_entries_validate_supplied_optional_evidence(kind: str, change: dict[str, object]) -> None:
    entry: dict[str, object] = {"ran": False, "reused_from": {"owner": "work/a", "run_id": "r"}}
    entry.update(change)
    if kind == "units":
        entry.update(name="lib", hash="1" * 64)
        value: object = [entry]
    else:
        entry.update(tree=TREE, command="checks")
        value = entry
    with pytest.raises(ValueError):
        gr.parse_gate_receipt(_mutated(**{kind: value}))


def test_evaluate_requires_at_least_one_unit_hash(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one unit hash"):
        ev(tmp_path, hashes={}, repo_wide_command=None)


def test_cached_lookup_equals_the_first_lookup_and_parses_nothing(tmp_path: Path, monkeypatch) -> None:
    put(tmp_path, "work/a", v2("20260928T110000Z-00000001", "2026-09-28T11:00:00Z", units=[green("lib", H_LIB)]))
    put(
        tmp_path,
        "work/b",
        v2("20260928T130000Z-00000000", "2026-09-28T13:00:00Z", units=[green("app", H_APP)], repo_wide=wide()),
    )
    (tmp_path / "work/c/references").mkdir(parents=True)
    (tmp_path / "work/c/references/03-gate-receipts.md").write_bytes(b"\xff")
    first = ev(tmp_path)
    calls: list[str] = []
    real = gr.parse_gate_receipt
    monkeypatch.setattr(gr, "parse_gate_receipt", lambda text: calls.append(text) or real(text))
    second = ev(tmp_path)
    assert second == first and calls == []
    assert second.satisfied and len(second.warnings) == 1
