"""Characterization goldens: the exact `--json` stdout of every command whose
projection lives in (or is moving to) `graph-works-wire`.

These were generated once, on the pre-move code, and every later change must
leave them byte-identical -- that is the whole of the C1 byte-identity proof.
Regenerate only with `GW_REGEN_JSON_GOLDENS=1`, which neither `just check` nor
CI ever sets. A diff in any golden after the move is a regression, not a
refresh.

Normalization is deliberately narrow and named:

* the per-test temporary root becomes `<tmp>` (both its raw and its resolved
  spelling, and both as-is and JSON-escaped, so Windows backslashes are
  covered);
* today's date (local and UTC) becomes `<today>`.

Anything else a case needs masked is a named `mask` on that case, with a
comment saying what it hides.

Cases that would reach a model, the code graph or the host (scan, ingest,
query, drift, platform, hooks, proposals) patch the *core* entry point the CLI
module imported -- the same seams the per-command suites patch -- so the
CLI's projection and encoding path is still the thing under test.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from code_graph_io.testing import raw_conn
from code_wiki_okf.entities.sync import SyncSummary
from graph_works_cli.cli import app
from graph_works_cli.util_cli import platform as platform_module
from graph_works_cli.wiki_cli import drift as drift_module
from graph_works_cli.wiki_cli import ingest as ingest_module
from graph_works_cli.wiki_cli import proposals as proposals_module
from graph_works_cli.wiki_cli import query as query_module
from graph_works_cli.wiki_cli import scan as scan_module
from graph_works_core.hooks import HooksResult
from graph_works_core.ingest.commands import IngestResult
from graph_works_core.lint_drift.propagate_drift import Candidate, DriftBrief, PropagateResult, Target
from graph_works_core.query.commands import QueryBrief, QueryPageBrief, QueryResult
from graph_works_core.scan.commands import ScanResult, StructuralSummary
from graph_works_core.scan.scan_contract import ApplyResult, ScanWorklist, worklist_payload
from graph_works_core.util.platform import Capability, PlatformReport, ProbeResult
from okf_ext.proposals import Proposal
from okf_io import load
from ruamel.yaml import YAML
from typer.testing import CliRunner

GOLDENS = Path(__file__).parent / "fixtures" / "json"
REGEN = os.environ.get("GW_REGEN_JSON_GOLDENS") == "1"

runner = CliRunner()


@dataclass
class Ctx:
    """One case's world: a temporary directory and the workspace inside it."""

    tmp: Path
    root: Path
    mp: pytest.MonkeyPatch

    @property
    def ws(self) -> list[str]:
        return ["--workspace", str(self.root)]

    def gw(self, *args: str) -> None:
        """A setup step. It must succeed; its output is not under test."""
        result = runner.invoke(app, [*args, *self.ws])
        assert result.exit_code == 0, result.output


@dataclass(frozen=True)
class Case:
    id: str
    argv: Callable[[Ctx], list[str]]
    setup: Callable[[Ctx], None] = lambda ctx: None
    exit_code: int = 0
    masks: tuple[Callable[[str], str], ...] = ()


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------


def items(ctx: Ctx) -> None:
    """work/feature-alpha, work/bug-fix (effort small), work/epic-epic, work/release-rel."""
    ctx.gw("work", "file", "--title", "Alpha", "--kind", "Feature", "--summary", "d", "--json")
    ctx.gw("work", "file", "--title", "Fix", "--kind", "Bug", "--summary", "d", "--effort", "small", "--json")
    ctx.gw("work", "file", "--title", "Epic", "--kind", "Epic", "--summary", "d", "--json")
    ctx.gw("work", "file", "--title", "Rel", "--kind", "Release", "--summary", "d", "--json")


def items_advanced(ctx: Ctx) -> None:
    items(ctx)
    ctx.gw("work", "advance", "work/bug-fix", "--json")


def decision_added(ctx: Ctx) -> None:
    items(ctx)
    ctx.gw("work", "decision", "add", "work/epic-epic", "--question", "q?", "--json")


def decision_superseded(ctx: Ctx) -> None:
    decision_added(ctx)
    ctx.gw("work", "decision", "answer", "work/epic-epic", "D-001", "--answer", "yes", "--json")
    ctx.gw("work", "decision", "supersede", "work/epic-epic", "D-001", "--question", "q2?", "--answer", "no", "--json")


def resolved_bug(ctx: Ctx) -> None:
    ctx.gw("work", "file", "--title", "Done", "--kind", "Bug", "--summary", "d", "--json")
    document = load(ctx.root / "okf" / "work" / "bug-done.md")
    document.set("work_status", "resolved")
    document.save()


def ingest_queue_item(ctx: Ctx) -> None:
    """A terminal item whose design was never ingested (test_work_cli_reads' fixture)."""
    page = ctx.root / "okf" / "work" / "bug-a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Bug\ntitle: bug-a\ndescription: d\nstatus: stable\n"
        "work_status: resolved\nphase: done\neffort: small\n"
        "opened: 2026-08-01\nupdated: 2026-08-02\naffects:\n- packages/a\n"
        "sources:\n  - id: design\n    resource: /work/bug-a/references/01-design.md\n"
        "    title: Design\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )
    artifact = ctx.root / "okf" / "work" / "bug-a" / "references" / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# spec\n", encoding="utf-8", newline="")


def tagged(ctx: Ctx) -> None:
    ctx.gw("work", "file", "--title", "Tagged", "--kind", "Feature", "--summary", "d", "--tags", "perf", "--json")


def crlf_member(ctx: Ctx) -> None:
    note = ctx.root / "okf" / "notes" / "crlf.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_bytes(b"---\ntitle: crlf\n---\r\n\r\nbody\r\n")


def configured_repo(ctx: Ctx) -> None:
    """One configured repository, as the ingest suite does."""
    repo = ctx.tmp / "repo"
    repo.mkdir()
    yaml = YAML()
    yaml.preserve_quotes = True
    manifest = ctx.root / "workspace.yaml"
    with manifest.open(encoding="utf-8") as handle:
        data = yaml.load(handle)
    data["repositories"] = {"repo-1": {"path": str(repo)}}
    data["ignore"] = []
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        yaml.dump(data, handle)
    ctx.mp.setattr(ingest_module, "state_gate_adapter", lambda config: object())


def ingest_brief(ctx: Ctx) -> None:
    configured_repo(ctx)
    brief = SimpleNamespace(
        as_data=lambda: {"source": "source.md", "kind": "reference", "sections": ["Intro"], "state_gate": None}
    )
    ctx.mp.setattr(ingest_module, "plan_ingest_brief", lambda *args, **kwargs: brief)


def ingest_result(ctx: Ctx) -> None:
    configured_repo(ctx)

    async def fake(*args: object, **kwargs: object) -> IngestResult:
        return IngestResult(
            ok=True,
            page="sources/demo.md",
            copy="sources/references/demo.md",
            title="Demo",
            source_kind="reference",
            entity_uri="pkg:demo",
            entity_page="packages/demo.md",
            written=("sources/demo.md", "sources/references/demo.md"),
            indexes_updated=("index.md",),
            proposals=(
                {
                    "lane": "concepts",
                    "title": "Demo concept",
                    "target": "concepts/demo.md",
                    "proposal": "Add the concept",
                    "status": "filed",
                },
            ),
        )

    ctx.mp.setattr(ingest_module, "run_ingest_source", fake)


def query_brief(ctx: Ctx) -> None:
    brief = QueryBrief(
        query="why",
        top_pages=(QueryPageBrief(path="concepts/a", excerpt="x", search_scores={"bm25": 1.0, "rrf": 0.2}),),
    )
    ctx.mp.setattr(query_module, "default_embedder", lambda: object())
    ctx.mp.setattr(query_module, "plan_query_brief", lambda *args, **kwargs: brief)


def query_result(ctx: Ctx) -> None:
    async def fake(*args: object, **kwargs: object) -> QueryResult:
        return QueryResult("answer", ["concepts/a", "concepts/b"], 2, {"concepts/a": {"rrf": 0.5}}, "orchestrated")

    ctx.mp.setattr(query_module, "default_embedder", lambda: object())
    ctx.mp.setattr(query_module, "run_query", fake)


def drift_graph(ctx: Ctx) -> None:
    """`drift` opens the code graph unconditionally; an empty store suffices."""
    graph_dir = ctx.root / ".gw" / "cache"
    graph_dir.mkdir(parents=True, exist_ok=True)
    raw_conn(graph_dir / "code.db", create=True).close()
    ctx.mp.setattr(drift_module, "resolve_repo", lambda layout, repo_name=None: (ctx.tmp / "repo", None))


def drift_brief(ctx: Ctx) -> None:
    drift_graph(ctx)
    candidate = Candidate(
        concept_id="packages/foo",
        resource="package:foo",
        title="foo",
        narrative="It does a thing.",
        last_updated_commit="abc1234",
        anchor=None,
        changed_files=("a.py",),
    )
    target = Target(concept_id="concepts/bar", title="bar", body="x", kind="concept", candidates=(candidate,))
    ctx.mp.setattr(drift_module, "plan_drift_brief", lambda *args, **kwargs: DriftBrief(targets=(target,)))


def drift_result(ctx: Ctx) -> None:
    drift_graph(ctx)

    async def fake(*args: object, **kwargs: object) -> PropagateResult:
        return PropagateResult(entities_considered=1, pages_judged=1, dry_run=True)

    ctx.mp.setattr(drift_module, "run_propagate_drift", fake)


def scan_normal(ctx: Ctx) -> None:
    async def fake(*args: object, **kwargs: object) -> ScanResult:
        return ScanResult(
            structural=StructuralSummary(entities=SyncSummary(written=("packages/demo",))),
            worklist=ScanWorklist(short_head="abc123"),
        )

    ctx.mp.setattr(scan_module, "run_scan", fake)


def scan_emit(ctx: Ctx) -> None:
    async def fake(*args: object, **kwargs: object) -> tuple[ScanWorklist, StructuralSummary]:
        return ScanWorklist(short_head="abc123"), StructuralSummary(entities=SyncSummary(written=("packages/demo",)))

    ctx.mp.setattr(scan_module, "build_scan_worklist", fake)


def scan_apply(ctx: Ctx) -> None:
    layout = scan_module.resolve_workspace(str(ctx.root))
    worklist_path = scan_module.scan_cache_dir(layout) / scan_module.WORKLIST_FILENAME
    worklist_path.parent.mkdir(parents=True, exist_ok=True)
    worklist_path.write_text(
        json.dumps(worklist_payload(ScanWorklist(short_head="abc123"))), encoding="utf-8", newline=""
    )
    ctx.mp.setattr(scan_module, "apply_scan_worklist", lambda **kwargs: ApplyResult(narrated=1, stamped=2))


def proposals(ctx: Ctx) -> None:
    proposal = Proposal(
        member="proposals/a.md",
        concept_id="proposals/a",
        target="concepts/a.md",
        title="A",
        description="d",
        page_status="proposed",
        raw_page_status="proposed",
        sources=({"resource": "sources/a.md"},),
        verified=({"by": "human", "at": "2026-09-01"},),
        malformed=None,
    )
    ctx.mp.setattr(proposals_module, "load_bundle", lambda root: object())
    ctx.mp.setattr(proposals_module, "list_proposals", lambda bundle, **kwargs: (proposal,))


def platform_report(ctx: Ctx) -> None:
    report = PlatformReport(
        schema_version=1,
        platform="win32",
        python="3.12.7",
        capabilities=(Capability("dispatch-backend", "workflow-orca", "available", "resolved", ("g1",), "m"),),
        probes=(ProbeResult("dispatch-backend", "unavailable", "orca is not on PATH", agrees_with_declared=False),),
    )
    ctx.mp.setattr(platform_module, "build_report", lambda **kwargs: report)


def hooks(*, action: str) -> Callable[[Ctx], None]:
    def setup(ctx: Ctx) -> None:
        (ctx.tmp / "repo").mkdir()

        def fake(act: str, feature: str, repo_root: Path) -> HooksResult:
            assert act == action
            return HooksResult(
                settings_path=repo_root / ".claude" / "settings.local.json",
                changed=True,
                added=("session-end-transcript-capture.sh",) if action == "enable" else (),
                removed=("session-end-transcript-capture.sh",) if action == "disable" else (),
                skipped=("some-other-hook.sh",) if action == "enable" else (),
            )

        ctx.mp.setattr("graph_works_cli.config_cli.main.apply_hooks", fake)

    return setup


# ---------------------------------------------------------------------------
# The case table
# ---------------------------------------------------------------------------


def _w(*args: str) -> Callable[[Ctx], list[str]]:
    """argv with `--workspace <root>` appended."""
    return lambda ctx: [*args, *ctx.ws]


CASES: tuple[Case, ...] = (
    # gw work
    Case("work-file", _w("work", "file", "--title", "Alpha", "--kind", "Feature", "--summary", "d", "--json")),
    Case("work-next", _w("work", "next", "work/feature-alpha", "--json"), items),
    Case("next-alias", _w("next", "work/feature-alpha", "--json"), items),
    Case("work-advance-dry-run", _w("work", "advance", "work/bug-fix", "--dry-run", "--json"), items),
    Case("work-advance", _w("work", "advance", "work/bug-fix", "--json"), items),
    Case(
        "work-record-placement",
        lambda ctx: [
            "work",
            "record-placement",
            "work/bug-fix",
            "--root",
            "work/bug-fix",
            "--phase",
            "design",
            "--worktree",
            str(ctx.tmp / "wt"),
            "--branch",
            "b/observed",
            "--json",
            *ctx.ws,
        ],
        items_advanced,
    ),
    Case("work-status", _w("work", "status", "--json"), items),
    Case("work-ingest-queue", _w("work", "ingest-queue", "--json"), ingest_queue_item),
    Case("work-lint", _w("work", "lint", "--json"), items),
    Case("work-regen-index", _w("work", "regen-index", "--json"), items),
    Case("work-reparent", _w("work", "reparent", "work/feature-alpha", "--parent", "work/epic-epic", "--json"), items),
    Case("work-adopt", _w("work", "adopt", "work/bug-fix", "--release", "work/release-rel", "--json"), items),
    Case("work-archive", _w("work", "archive", "work/bug-done", "--json"), resolved_bug),
    Case("work-decision-add", _w("work", "decision", "add", "work/epic-epic", "--question", "q?", "--json"), items),
    Case(
        "work-decision-answer",
        _w("work", "decision", "answer", "work/epic-epic", "D-001", "--answer", "yes", "--json"),
        decision_added,
    ),
    Case(
        "work-decision-supersede",
        _w("work", "decision", "supersede", "work/epic-epic", "D-001", "--question", "q2?", "--answer", "no", "--json"),
        decision_added,
    ),
    Case("work-decision-list", _w("work", "decision", "list", "work/epic-epic", "--json"), decision_superseded),
    Case(
        "work-decision-overturn",
        _w(
            "work",
            "decision",
            "overturn",
            "work/epic-epic",
            "D-002",
            "--answer",
            "later",
            "--follow-up-title",
            "Redo",
            "--json",
        ),
        decision_superseded,
    ),
    Case("work-orchestrate-epic", _w("work", "orchestrate", "work/epic-epic", "--json"), items),
    Case("work-orchestrate-leaf", _w("work", "orchestrate", "work/feature-alpha", "--json"), items),
    Case("work-reconcile-context", _w("work", "reconcile-context", "work/epic-epic", "--json"), items),
    # top level
    Case(
        "bootstrap-plan",
        lambda ctx: ["bootstrap", "--topic", "Demo", "--workspace", str(ctx.tmp / "fresh"), "--dry-run", "--json"],
    ),
    Case(
        "bootstrap-apply", lambda ctx: ["bootstrap", "--topic", "Demo", "--workspace", str(ctx.tmp / "fresh"), "--json"]
    ),
    Case("scan-normal", _w("scan", "--json"), scan_normal),
    Case("scan-emit", _w("scan", "--emit-worklist"), scan_emit),
    Case("scan-apply", _w("scan", "--apply", "--results-dir", "results", "--short-head", "abc123"), scan_apply),
    Case(
        "ingest-brief",
        lambda ctx: ["ingest", "--source", str(ctx.tmp / "source.md"), "--json", *ctx.ws],
        ingest_brief,
    ),
    Case(
        "ingest-result",
        lambda ctx: ["ingest", "--source", str(ctx.tmp / "source.md"), "--json", "--backend", "bedrock", *ctx.ws],
        ingest_result,
    ),
    Case("query-brief", _w("query", "--query", "why", "--json"), query_brief),
    Case("query-result", _w("query", "--query", "why", "--backend", "bedrock", "--json"), query_result),
    # gw wiki
    Case("wiki-lint", _w("wiki", "lint", "--json"), items),
    Case("wiki-drift-brief", _w("wiki", "drift", "--backend", "claude_code", "--json"), drift_brief),
    Case("wiki-drift-result", _w("wiki", "drift", "--backend", "bedrock", "--json"), drift_result),
    Case("wiki-stats", _w("wiki", "stats", "--json"), items),
    Case("wiki-proposals", _w("wiki", "proposals", "--json"), proposals),
    Case("wiki-tags-inventory", _w("wiki", "tags", "inventory", "--json"), tagged),
    Case("wiki-tags-gate", _w("wiki", "tags", "gate", "--json"), tagged),
    # gw config
    Case("config-get", _w("config", "get", "topic", "--json")),
    Case("config-list", _w("config", "list", "--json")),
    Case("config-set", _w("config", "set", "workflow.auto_drive.max_parallel", "3", "--json")),
    Case("config-unset", _w("config", "unset", "workflow.auto_drive.max_parallel", "--json")),
    Case("config-sync", _w("config", "sync", "--json")),
    Case(
        "config-hooks-enable",
        lambda ctx: ["config", "hooks", "enable", "transcript", "--repo", str(ctx.tmp / "repo"), "--json"],
        hooks(action="enable"),
    ),
    Case(
        "config-hooks-disable",
        lambda ctx: ["config", "hooks", "disable", "transcript", "--repo", str(ctx.tmp / "repo"), "--json"],
        hooks(action="disable"),
    ),
    # gw util
    Case("util-log", _w("util", "log", "--op", "note", "--title", "Hello", "--json")),
    Case("util-platform", lambda ctx: ["util", "platform", "--json"], platform_report),
    Case("util-tokens", _w("util", "tokens", "--dry-run", "--json"), items),
    Case("util-line-endings", _w("util", "line-endings", "--fix", "--json"), crlf_member),
)


# ---------------------------------------------------------------------------
# Normalization and comparison
# ---------------------------------------------------------------------------


def _spellings(path: Path) -> list[str]:
    raw = {str(path), str(path.resolve())}
    escaped = {json.dumps(value)[1:-1] for value in raw}
    return sorted(raw | escaped, key=len, reverse=True)


def normalize(text: str, tmp: Path) -> str:
    for spelling in _spellings(tmp):
        text = text.replace(spelling, "<tmp>")
    for today in {date.today().isoformat(), datetime.now(UTC).date().isoformat()}:
        text = text.replace(today, "<today>")
    return text


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No developer env, and no git repo above the working directory."""
    monkeypatch.delenv("GRAPH_WORKS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)


def test_every_case_id_is_unique() -> None:
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))


def test_no_orphan_golden() -> None:
    """A golden with no case is a characterization nobody runs any more."""
    on_disk = {path.name.removesuffix(".golden.json") for path in GOLDENS.glob("*.golden.json")}
    assert on_disk <= {case.id for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_json_output_is_byte_identical(case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "works"
    boot = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert boot.exit_code == 0, boot.output
    (tmp_path / "source.md").write_text("# Source\n", encoding="utf-8", newline="")
    ctx = Ctx(tmp=tmp_path, root=root, mp=monkeypatch)
    case.setup(ctx)

    result = runner.invoke(app, case.argv(ctx))

    assert result.exit_code == case.exit_code, result.output
    json.loads(result.stdout)  # one JSON document on stdout, nothing else
    stdout = normalize(result.stdout, tmp_path)
    for mask in case.masks:
        stdout = mask(stdout)
    golden = GOLDENS / f"{case.id}.golden.json"
    if REGEN:
        GOLDENS.mkdir(parents=True, exist_ok=True)
        golden.write_text(stdout, encoding="utf-8", newline="")
    assert golden.is_file(), f"no golden for {case.id}; generate on the pre-move code with GW_REGEN_JSON_GOLDENS=1"
    assert stdout == golden.read_text(encoding="utf-8")
