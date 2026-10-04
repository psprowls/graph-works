"""The stat-validated gate receipt cache: re-parse only what changed."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from graph_works_core.orchestrate import gate_index

GLOB = "work/**/references/03-gate-receipts.md"


class CountingParse:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        self.calls.append(text)
        if not text.startswith("owner:"):
            raise ValueError("malformed gate receipt")
        owner, _, rest = text.partition("\n")
        return owner.removeprefix("owner:"), [{"n": line} for line in rest.splitlines() if line]


def put(bundle: Path, owner: str, body: str, *, at: str | None = None) -> Path:
    target = bundle / (at or owner) / "references" / "03-gate-receipts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"owner:{owner}\n{body}", encoding="utf-8", newline="\n")
    return target


def read(bundle: Path, cache: Path, parse: CountingParse) -> gate_index.IndexRead:
    return gate_index.read_index(bundle, cache, glob=GLOB, parse=parse)


def test_unchanged_files_are_not_reparsed(tmp_path: Path) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    put(bundle, "work/a", "r1\n")
    put(bundle, "work/b", "r2\n")
    first = read(bundle, cache, CountingParse())
    assert first.parsed == ("work/a/references/03-gate-receipts.md", "work/b/references/03-gate-receipts.md")
    again = read(bundle, cache, parse2 := CountingParse())
    assert again.parsed == () and parse2.calls == []
    assert [(r.owner, r.runs) for r in again.receipts] == [("work/a", ({"n": "r1"},)), ("work/b", ({"n": "r2"},))]


def test_touched_added_removed_and_archived_files_are_reflected(tmp_path: Path) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    a = put(bundle, "work/a", "r1\n")
    put(bundle, "work/b", "r2\n")
    read(bundle, cache, CountingParse())
    a.write_text("owner:work/a\nr1\nr3\n", encoding="utf-8", newline="\n")
    os.utime(a, ns=(a.stat().st_atime_ns, a.stat().st_mtime_ns + 1_000_000))
    (bundle / "work/b/references/03-gate-receipts.md").unlink()
    put(bundle, "work/b", "r2\n", at="work/_archive/b")
    put(bundle, "work/c", "r4\n")
    result = read(bundle, cache, parse := CountingParse())
    assert len(parse.calls) == 3
    assert {r.rel: len(r.runs) for r in result.receipts} == {
        "work/a/references/03-gate-receipts.md": 2,
        "work/_archive/b/references/03-gate-receipts.md": 1,
        "work/c/references/03-gate-receipts.md": 1,
    }
    stored = json.loads(gate_index.cache_path(cache).read_text(encoding="utf-8"))
    assert set(stored["files"]) == {r.rel for r in result.receipts}


def test_malformed_and_non_utf8_files_are_cached_as_errors(tmp_path: Path) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    put(bundle, "work/a", "")
    bad = bundle / "work/a/references/03-gate-receipts.md"
    bad.write_text("nonsense", encoding="utf-8", newline="\n")
    raw = bundle / "work/b/references/03-gate-receipts.md"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"\xff\xfe")
    first = read(bundle, cache, CountingParse())
    assert [r.error is not None for r in first.receipts] == [True, True]
    again = read(bundle, cache, parse := CountingParse())
    assert parse.calls == [] and [r.error is not None for r in again.receipts] == [True, True]


@pytest.mark.parametrize(
    "content", ["{not json", '{"version": 99, "files": {}}', "[]", '{"version": 1, "files": {"x": 3}}']
)
def test_a_corrupt_or_foreign_cache_rebuilds(tmp_path: Path, content: str) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    put(bundle, "work/a", "r1\n")
    gate_index.cache_path(cache).parent.mkdir(parents=True)
    gate_index.cache_path(cache).write_text(content, encoding="utf-8", newline="\n")
    result = read(bundle, cache, parse := CountingParse())
    assert len(parse.calls) == 1 and result.receipts[0].runs == ({"n": "r1"},) and result.warnings == ()


def test_an_unwritable_cache_warns_and_still_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    put(bundle, "work/a", "r1\n")
    monkeypatch.setattr(gate_index, "_write", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    result = read(bundle, cache, CountingParse())
    assert result.receipts[0].runs == ({"n": "r1"},)
    assert len(result.warnings) == 1 and "read-only" in result.warnings[0]


def test_write_through_refreshes_an_entry_even_when_the_signature_is_unchanged(tmp_path: Path) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    target = put(bundle, "work/a", "r1\n")
    read(bundle, cache, CountingParse())
    stat = target.stat()
    target.write_text("owner:work/a\nr9\n", encoding="utf-8", newline="\n")  # same size
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert (
        gate_index.write_through(bundle, cache, "work/a/references/03-gate-receipts.md", parse=CountingParse()) is None
    )
    result = read(bundle, cache, parse := CountingParse())
    assert parse.calls == [] and result.receipts[0].runs == ({"n": "r9"},)


def test_write_through_of_a_vanished_file_writes_nothing(tmp_path: Path) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    assert (
        gate_index.write_through(bundle, cache, "work/x/references/03-gate-receipts.md", parse=CountingParse()) is None
    )
    assert not gate_index.cache_path(cache).exists()


def test_a_read_does_not_overwrite_a_concurrent_write_through(tmp_path: Path) -> None:
    bundle, cache = tmp_path / "okf", tmp_path / "cache"
    put(bundle, "work/a", "r1\n")
    b_rel = "work/b/references/03-gate-receipts.md"

    class Racing(CountingParse):
        def __call__(self, text: str) -> tuple[str, list[dict[str, Any]]]:
            result = super().__call__(text)
            if not (bundle / b_rel).exists():  # a concurrent gw write lands mid-read
                put(bundle, "work/b", "r2\n")
                assert gate_index.write_through(bundle, cache, b_rel, parse=CountingParse()) is None
            return result

    read(bundle, cache, Racing())
    result = read(bundle, cache, parse := CountingParse())
    assert parse.calls == [] and [r.owner for r in result.receipts] == ["work/a", "work/b"]


def test_cache_version_is_pinned_to_the_run_data_field_set() -> None:
    from graph_works_core.orchestrate.gate_receipts import GateRun, RepoWideEntry, Reuse, UnitEntry, run_data

    reuse = Reuse(owner="o", run_id="r")
    run = GateRun(
        run_id="r", repo="c", worktree="w", head="h", tree="t", clean=True, tree_changed=False, scope="full",
        command="c", names=("n",), exit=0, log_path="l", log_tail="", started="s", duration_s=1.0,
        manifest_hash="m",
        repo_wide=RepoWideEntry(tree="t", command="c", ran=True, exit=0, duration_s=1.0, reused_from=reuse),
        units=(UnitEntry(name="u", hash="h", ran=True, exit=0, log_path="l", duration_s=1.0, reused_from=reuse),),
    )  # fmt: skip
    data: Any = run_data(run)
    # The cache stores run_data output verbatim. If this fails, the cached shape changed: bump
    # gate_index.CACHE_VERSION (old caches then rebuild), and only then update these literals.
    assert gate_index.CACHE_VERSION == 1
    assert sorted(data) == [
        "clean",
        "command",
        "duration_s",
        "exit",
        "head",
        "log_path",
        "log_tail",
        "manifest_hash",
        "names",
        "repo",
        "repo_wide",
        "run_id",
        "scope",
        "started",
        "tree",
        "tree_changed",
        "units",
        "worktree",
    ]
    assert sorted(data["repo_wide"]) == ["command", "duration_s", "exit", "ran", "reused_from", "tree"]
    assert sorted(data["units"][0]) == ["duration_s", "exit", "hash", "log_path", "name", "ran", "reused_from"]
