"""The typer app: every command, both output modes, every exit path."""

import json
from datetime import date

from doc_wiki_okf.cli import app
from doc_wiki_okf.resources import SEED_RELATIVE_PATHS
from proposal_helpers import TODAY
from typer.testing import CliRunner

runner = CliRunner()
DAY = TODAY.isoformat()


def _init(root) -> None:
    assert runner.invoke(app, ["init", str(root), "--today", DAY]).exit_code == 0


def _file_one(root, *, title="Bulk Write Staging Protocol", lane="adr", resource="sources/a.md"):
    return runner.invoke(
        app,
        [
            "proposal",
            "file",
            str(root),
            "--lane",
            lane,
            "--title",
            title,
            "--description",
            "Why this page.",
            "--id",
            "src-a",
            "--resource",
            resource,
            "--rationale",
            "It settles it.",
            "--evidence",
            "Staging precedes any live write.",
            "--by",
            "agent:test",
            "--today",
            DAY,
        ],
    )


def test_init_writes_the_bundle_and_every_declaration(tmp_path) -> None:
    root = tmp_path / "b"
    result = runner.invoke(app, ["init", str(root), "--today", DAY])
    assert result.exit_code == 0
    assert (root / "index.md").is_file()
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file()
    assert "refused" not in result.stderr


def test_init_dry_run_writes_nothing(tmp_path) -> None:
    root = tmp_path / "b"
    result = runner.invoke(app, ["init", str(root), "--today", DAY, "--dry-run"])
    assert result.exit_code == 0
    assert not root.exists()
    assert "would write index.md" in result.output


def test_init_is_idempotent(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    second = runner.invoke(app, ["init", str(root), "--today", DAY])
    assert second.exit_code == 0
    assert "wrote " not in second.stdout


def test_a_bad_today_exits_one(tmp_path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path / "b"), "--today", "not-a-date"])
    assert result.exit_code == 1
    assert "not an ISO 8601 date" in result.stderr


def test_proposals_lists_what_was_filed(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    assert _file_one(root).exit_code == 0
    result = runner.invoke(app, ["proposals", str(root)])
    assert result.exit_code == 0
    assert "adrs/bulk-write-staging-protocol.md" in result.stdout
    assert "proposed" in result.stdout


def test_proposals_json_carries_the_documented_shape(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    result = runner.invoke(app, ["proposals", str(root), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert set(payload[0]) == {
        "member",
        "target",
        "lane",
        "title",
        "description",
        "page_status",
        "sources",
        "verified",
        "malformed",
    }
    assert payload[0]["lane"] == "adr"
    assert payload[0]["page_status"] == "proposed"
    assert payload[0]["sources"][0]["resource"] == "sources/a.md"


def test_proposals_filters_by_page_status(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    result = runner.invoke(app, ["proposals", str(root), "--page-status", "approved", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_proposals_rejects_an_unknown_page_status(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = runner.invoke(app, ["proposals", str(root), "--page-status", "pending"])
    assert result.exit_code == 1
    assert "pending" in result.stderr


def test_show_prints_the_review_body(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    result = runner.invoke(app, ["proposal", "show", str(root), "adrs/bulk-write-staging-protocol.md"])
    assert result.exit_code == 0
    assert "## Suggested Action" in result.stdout
    assert "Create new Explanation page `adrs/bulk-write-staging-protocol.md`." in result.stdout
    assert "Staging precedes any live write." in result.stdout


def test_show_exits_one_for_an_unknown_target(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = runner.invoke(app, ["proposal", "show", str(root), "adrs/nope.md"])
    assert result.exit_code == 1
    assert "no proposal" in result.stderr


def test_a_root_that_is_not_a_directory_exits_one(tmp_path) -> None:
    result = runner.invoke(app, ["proposals", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "not a directory" in result.stderr


def test_file_writes_the_proposal_and_reports_it(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = _file_one(root)
    assert result.exit_code == 0
    assert "wrote proposals/" in result.stdout
    assert (root / "proposals").is_dir()


def test_file_dry_run_writes_nothing(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = runner.invoke(
        app,
        [
            "proposal",
            "file",
            str(root),
            "--lane",
            "adr",
            "--title",
            "T",
            "--description",
            "D",
            "--id",
            "a",
            "--resource",
            "sources/a.md",
            "--rationale",
            "r",
            "--today",
            DAY,
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "would create proposals/" in result.stdout
    assert not (root / "proposals").exists()


def test_file_carries_every_repeated_evidence_bullet(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    runner.invoke(
        app,
        [
            "proposal",
            "file",
            str(root),
            "--lane",
            "adr",
            "--title",
            "T",
            "--description",
            "D",
            "--id",
            "a",
            "--resource",
            "sources/a.md",
            "--rationale",
            "r",
            "--evidence",
            "one",
            "--evidence",
            "two",
            "--today",
            DAY,
        ],
    )
    payload = json.loads(runner.invoke(app, ["proposals", str(root), "--json"]).stdout)
    assert payload[0]["sources"][0]["evidence"] == ["one", "two"]


def test_refiling_the_same_source_reports_nothing_to_do(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    result = _file_one(root)
    assert result.exit_code == 0
    assert "nothing to do" in result.stdout


def test_approve_flips_the_status_and_records_the_actor(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    result = runner.invoke(
        app,
        ["proposal", "approve", str(root), "adrs/bulk-write-staging-protocol.md", "--by", "human:pat", "--today", DAY],
    )
    assert result.exit_code == 0
    payload = json.loads(runner.invoke(app, ["proposals", str(root), "--json"]).stdout)
    assert payload[0]["page_status"] == "approved"
    assert payload[0]["verified"][0]["by"] == "human:pat"


def test_reject_flips_the_status_too(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    assert (
        runner.invoke(
            app, ["proposal", "reject", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY]
        ).exit_code
        == 0
    )
    payload = json.loads(runner.invoke(app, ["proposals", str(root), "--json"]).stdout)
    assert payload[0]["page_status"] == "rejected"


def test_deciding_twice_refuses_on_stderr(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    runner.invoke(app, ["proposal", "approve", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY])
    second = runner.invoke(
        app, ["proposal", "approve", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY]
    )
    assert second.exit_code == 1
    assert "not-proposed" in second.stderr


def test_promote_writes_the_dated_page_and_retargets_the_ledger(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    runner.invoke(app, ["proposal", "approve", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY])
    result = runner.invoke(
        app, ["proposal", "promote", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY]
    )
    assert result.exit_code == 0
    assert (root / "adrs/2026-08-12-bulk-write-staging-protocol.md").is_file()
    payload = json.loads(runner.invoke(app, ["proposals", str(root), "--json"]).stdout)
    assert payload[0]["page_status"] == "created"
    assert payload[0]["target"] == "adrs/2026-08-12-bulk-write-staging-protocol.md"


def test_promoting_an_unapproved_proposal_refuses(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    result = runner.invoke(
        app, ["proposal", "promote", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY]
    )
    assert result.exit_code == 1
    assert "not-approved" in result.stderr


def test_promote_json_reports_both_writes(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    _file_one(root)
    runner.invoke(app, ["proposal", "approve", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY])
    result = runner.invoke(
        app,
        ["proposal", "promote", str(root), "adrs/bulk-write-staging-protocol.md", "--today", DAY, "--json"],
    )
    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert [write["mode"] for write in payload["writes"]] == ["create", "update"]


def _old_proposal() -> str:
    return (
        "---\n"
        "kind: adr\n"
        "mode: create_new\n"
        "target_slug: bulk-write-staging-protocol\n"
        "title: Bulk multi-file writes stage to temp siblings\n"
        "status: proposed\n"
        "origins:\n"
        "- ref: sources/2026-08-spec\n"
        "  source: ingest\n"
        "  rationale: It settles it.\n"
        "---\n"
        "<!-- machine body -->\n"
    )


def _seed_old(root, member="proposals/adr-bulk.md") -> None:
    _init(root)
    target = root / member
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_old_proposal(), encoding="utf-8")


def test_migrate_previews_by_default_and_writes_nothing(tmp_path) -> None:
    root = tmp_path / "b"
    _seed_old(root)
    result = runner.invoke(app, ["migrate", str(root), "--today", DAY])
    assert result.exit_code == 0
    assert "would rewrite proposals/adr-bulk.md" in result.stdout
    assert "-> proposals/adrs-bulk-write-staging-protocol.md" in result.stdout
    assert (root / "proposals" / "adr-bulk.md").read_text(encoding="utf-8") == _old_proposal()


def test_migrate_apply_rewrites_and_moves(tmp_path) -> None:
    root = tmp_path / "b"
    _seed_old(root)
    result = runner.invoke(app, ["migrate", str(root), "--apply", "--today", DAY])
    assert result.exit_code == 0
    assert not (root / "proposals" / "adr-bulk.md").exists()
    assert (root / "proposals" / "adrs-bulk-write-staging-protocol.md").is_file()


def test_migrate_json_carries_the_plan_and_the_placements(tmp_path) -> None:
    root = tmp_path / "b"
    _seed_old(root)
    result = runner.invoke(app, ["migrate", str(root), "--json", "--today", DAY])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["applied"] is False
    assert payload["writes"] == ["proposals/adr-bulk.md"]
    assert payload["placements"] == {"proposals/adr-bulk.md": "proposals/adrs-bulk-write-staging-protocol.md"}
    assert payload["refusals"] == []


def test_migrate_reports_a_refusal_and_still_lists_its_neighbours(tmp_path) -> None:
    root = tmp_path / "b"
    _seed_old_and_bad(root)

    result = runner.invoke(app, ["migrate", str(root), "--json", "--today", DAY])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["writes"] == ["proposals/adr-bulk.md"]
    assert payload["refusals"] == [
        {
            "member": "proposals/adr-bad.md",
            "kind": "unknown-kind",
            "detail": payload["refusals"][0]["detail"],
        }
    ]


def test_migrate_preview_reports_a_refusal_in_plain_text(tmp_path) -> None:
    root = tmp_path / "b"
    _seed_old_and_bad(root)

    result = runner.invoke(app, ["migrate", str(root), "--today", DAY])
    assert result.exit_code == 1
    assert "would rewrite proposals/adr-bulk.md" in result.stdout
    assert "refused proposals/adr-bad.md (unknown-kind)" in result.stderr


def test_migrate_on_a_clean_bundle_says_nothing_to_do(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = runner.invoke(app, ["migrate", str(root), "--today", DAY])
    assert result.exit_code == 0
    assert "nothing to do" in result.stdout


def _seed_old_and_bad(root) -> None:
    """One migratable proposal, one refused neighbour (`kind: pattern`)."""
    _seed_old(root)
    bad = root / "proposals" / "adr-bad.md"
    bad.write_text(_old_proposal().replace("kind: adr", "kind: pattern"), encoding="utf-8")


def test_migrate_apply_with_a_mix_of_success_and_refusal(tmp_path) -> None:
    root = tmp_path / "b"
    _seed_old_and_bad(root)

    result = runner.invoke(app, ["migrate", str(root), "--apply", "--today", DAY])
    assert result.exit_code == 1
    assert "rewrote proposals/adr-bulk.md" in result.stdout
    assert "moved proposals/adr-bulk.md -> proposals/adrs-bulk-write-staging-protocol.md" in result.stdout
    assert "refused proposals/adr-bad.md (unknown-kind)" in result.stderr
    assert not (root / "proposals" / "adr-bulk.md").exists()
    assert (root / "proposals" / "adrs-bulk-write-staging-protocol.md").is_file()
    assert (root / "proposals" / "adr-bad.md").exists()

    json_root = tmp_path / "b-json"
    _seed_old_and_bad(json_root)
    result = runner.invoke(app, ["migrate", str(json_root), "--apply", "--json", "--today", DAY])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert payload["written"] == ["proposals/adr-bulk.md"]
    assert payload["moved"] == [["proposals/adr-bulk.md", "proposals/adrs-bulk-write-staging-protocol.md"]]
    assert payload["refusals"] == [
        {
            "member": "proposals/adr-bad.md",
            "kind": "unknown-kind",
            "detail": payload["refusals"][0]["detail"],
        }
    ]


def test_migrate_apply_on_a_missing_root_refuses(tmp_path) -> None:
    missing = tmp_path / "nope"
    result = runner.invoke(app, ["migrate", str(missing), "--apply", "--today", DAY])
    assert result.exit_code == 1
    assert "not a directory" in result.stderr


def _ingest_workspace(tmp_path):
    """A workspace with a `wiki/`, and material that need not live inside it."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "material" / "specs").mkdir(parents=True)
    (tmp_path / "material" / "specs" / "auth.md").write_text("# Auth Spec\n\nBody.", encoding="utf-8")
    return tmp_path


def test_ingest_briefs_a_batch(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    result = runner.invoke(
        app, ["ingest", str(workspace / "material" / "specs"), "--workspace", str(workspace), "--kind", "specs"]
    )
    assert result.exit_code == 0
    assert "batch specs" in result.stdout
    assert "auth.md" in result.stdout


def test_ingest_batch_json_matches_as_data(tmp_path):
    from doc_wiki_okf.ingest import plan_batch_brief

    workspace = _ingest_workspace(tmp_path)
    result = runner.invoke(
        app,
        ["ingest", str(workspace / "material" / "specs"), "--workspace", str(workspace), "--kind", "specs", "--json"],
    )
    assert result.exit_code == 0
    expected = plan_batch_brief(
        workspace / "material" / "specs", kind="specs", repo=workspace, workspace_root=workspace
    )
    assert expected is not None
    assert json.loads(result.stdout) == expected.as_data()


def test_ingest_all_removes_the_cap(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    for i in range(12):
        (workspace / "material" / "specs" / f"s{i:02d}.md").write_text("# S\n", encoding="utf-8")
    capped = runner.invoke(
        app,
        ["ingest", str(workspace / "material" / "specs"), "--workspace", str(workspace), "--kind", "specs", "--json"],
    )
    uncapped = runner.invoke(
        app,
        [
            "ingest",
            str(workspace / "material" / "specs"),
            "--workspace",
            str(workspace),
            "--kind",
            "specs",
            "--json",
            "--all",
        ],
    )
    assert json.loads(capped.stdout)["unit_count"] == 10
    assert json.loads(uncapped.stdout)["unit_count"] == 13


def test_ingest_limit_sets_the_cap(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    for i in range(5):
        (workspace / "material" / "specs" / f"s{i:02d}.md").write_text("# S\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "ingest",
            str(workspace / "material" / "specs"),
            "--workspace",
            str(workspace),
            "--kind",
            "specs",
            "--json",
            "--limit",
            "2",
        ],
    )
    payload = json.loads(result.stdout)
    assert payload["unit_count"] == 2
    assert payload["limited"] is True


def test_ingest_briefs_a_plain_folder(tmp_path):
    from doc_wiki_okf.ingest import plan_folder_brief

    workspace = _ingest_workspace(tmp_path)
    folder = workspace / "material" / "examples" / "demo"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("# A\n", encoding="utf-8")

    human = runner.invoke(app, ["ingest", str(folder), "--workspace", str(workspace)])
    payload = runner.invoke(app, ["ingest", str(folder), "--workspace", str(workspace), "--json"])

    assert human.exit_code == 0
    assert "1 file" in human.stdout
    assert json.loads(payload.stdout) == plan_folder_brief(folder, repo=workspace, workspace_root=workspace).as_data()


def test_ingest_briefs_a_skill_directory_as_a_folder(tmp_path):
    """Design spec §6.1: skill detection is absent from this cascade."""
    workspace = _ingest_workspace(tmp_path)
    skill = workspace / "material" / "skills" / "writing-plans"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Writing Plans\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(skill), "--workspace", str(workspace), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["is_folder"] is True


def test_ingest_refuses_a_crowded_folder(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    folder = workspace / "material" / "examples" / "huge"
    folder.mkdir(parents=True)
    for i in range(201):
        (folder / f"f{i:03d}.md").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(folder), "--workspace", str(workspace)])

    assert result.exit_code == 1
    assert "folder-too-large" in result.stderr


def test_ingest_without_a_kind_briefs_a_directory_as_a_folder(tmp_path):
    """S-H: the automatic batch arm is gone -- batch is what `--kind` asks for."""
    workspace = _ingest_workspace(tmp_path)
    result = runner.invoke(
        app, ["ingest", str(workspace / "material" / "specs"), "--workspace", str(workspace), "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["is_folder"] is True


def test_ingest_a_single_document_without_a_source_type_exits_one(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    source = workspace / "material" / "specs" / "auth.md"
    result = runner.invoke(app, ["ingest", str(source), "--workspace", str(workspace)])
    assert result.exit_code == 1
    assert "--source-type is required" in result.stderr


def test_ingest_kind_on_a_file_exits_one(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    source = workspace / "material" / "specs" / "auth.md"
    result = runner.invoke(app, ["ingest", str(source), "--workspace", str(workspace), "--kind", "specs"])
    assert result.exit_code == 1
    assert "--kind briefs a directory" in result.stderr


def test_ingest_briefs_a_single_document(tmp_path):
    from doc_wiki_okf.ingest import plan_document_brief

    workspace = _ingest_workspace(tmp_path)
    source = workspace / "material" / "specs" / "auth.md"

    human = runner.invoke(
        app, ["ingest", str(source), "--workspace", str(workspace), "--source-type", "spec", "--today", "2026-08-12"]
    )
    payload = runner.invoke(
        app,
        [
            "ingest",
            str(source),
            "--workspace",
            str(workspace),
            "--source-type",
            "spec",
            "--today",
            "2026-08-12",
            "--json",
        ],
    )

    assert human.exit_code == 0
    assert "Auth Spec" in human.stdout
    assert "sources/2026-08-auth-spec.md" in human.stdout
    assert (
        json.loads(payload.stdout)
        == plan_document_brief(
            source,
            wiki=workspace / "wiki",
            repo=workspace,
            workspace_root=workspace,
            today=date(2026, 8, 12),
            source_type="spec",
        ).as_data()
    )


def test_ingest_today_decides_the_source_page(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    source = workspace / "material" / "specs" / "auth.md"
    result = runner.invoke(
        app,
        [
            "ingest",
            str(source),
            "--workspace",
            str(workspace),
            "--source-type",
            "spec",
            "--today",
            "2026-01-05",
            "--json",
        ],
    )
    assert json.loads(result.stdout)["suggested_summary_path"] == "sources/2026-01-auth-spec.md"


def test_ingest_rejects_a_path_that_is_not_there(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    result = runner.invoke(
        app, ["ingest", str(workspace / "material" / "specs" / "nope.md"), "--workspace", str(workspace)]
    )
    assert result.exit_code == 1
    assert "no such file or directory" in result.stderr


def test_ingest_rejects_a_bad_today(tmp_path):
    workspace = _ingest_workspace(tmp_path)
    result = runner.invoke(
        app,
        [
            "ingest",
            str(workspace / "material" / "specs" / "auth.md"),
            "--workspace",
            str(workspace),
            "--source-type",
            "spec",
            "--today",
            "nope",
        ],
    )
    assert result.exit_code == 1


def test_ingest_an_absolute_source_resolves_against_a_default_workspace(tmp_path, monkeypatch):
    """An absolute SOURCE with `--workspace` omitted must still resolve correctly.

    `--workspace` defaults to `Path(".")`, which only names the right directory
    once resolved against `cwd`. Regression for the bug where an unresolved
    relative default failed `relative_to` against the absolute, resolved
    SOURCE.
    """
    workspace = _ingest_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    source = workspace / "material" / "specs" / "auth.md"

    result = runner.invoke(app, ["ingest", str(source), "--source-type", "spec", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["source_type"] == "spec"


def test_ingest_a_relative_workspace_still_resolves_correctly(tmp_path, monkeypatch):
    """A relative `--workspace` (e.g. `.`) must resolve to the same effective
    root as an absolute one.
    """
    workspace = _ingest_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    source = workspace / "material" / "specs" / "auth.md"

    result = runner.invoke(app, ["ingest", str(source), "--workspace", ".", "--source-type", "spec", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["source_type"] == "spec"


def test_ingest_accepts_an_explicit_repo(tmp_path):
    """`--repo` is what a relative SOURCE resolves against; it need not equal
    `--workspace`.
    """
    workspace = _ingest_workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source = workspace / "material" / "specs" / "auth.md"

    result = runner.invoke(
        app,
        ["ingest", str(source), "--workspace", str(workspace), "--repo", str(repo), "--source-type", "spec", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_type"] == "spec"
    assert payload["title"] == "Auth Spec"


def test_ingest_accepts_an_explicit_wiki(tmp_path):
    """`--wiki` is the bundle root the source page is checked against; a
    custom one, not `<workspace>/wiki`, must decide `merge_mode`.
    """
    workspace = _ingest_workspace(tmp_path)
    custom_wiki = tmp_path / "custom-wiki"
    (custom_wiki / "sources").mkdir(parents=True)
    (custom_wiki / "sources" / "2026-08-auth-spec.md").write_text("# Auth Spec\n", encoding="utf-8")
    source = workspace / "material" / "specs" / "auth.md"

    result = runner.invoke(
        app,
        [
            "ingest",
            str(source),
            "--workspace",
            str(workspace),
            "--wiki",
            str(custom_wiki),
            "--source-type",
            "spec",
            "--today",
            "2026-08-12",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["merge_mode"] is True


def _material(tmp_path, name="auth.md", data=b"# Auth Spec\n\nBody.\n"):
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    path = outside / name
    path.write_bytes(data)
    return path


def _add_source(root, material, *flags, **overrides):
    """The option-carrying invocation. *flags* carries bare flags like
    `--dry-run`, which cannot ride the name/value mapping."""
    args = {
        "--title": "Auth Spec",
        "--description": "The authentication specification.",
        "--source-type": "spec",
        "--origin": "https://example.invalid/auth-spec",
        "--by": "agent:test",
        "--today": DAY,
    }
    args.update(overrides)
    flat = [item for pair in args.items() for item in pair]
    return runner.invoke(app, ["source", "add", str(root), str(material), *flat, *flags])


def test_source_add_writes_the_page_and_the_copy(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = _add_source(root, _material(tmp_path))

    assert result.exit_code == 0, result.output
    assert (root / "sources" / "2026-08-auth-spec.md").is_file()
    assert (root / "sources" / "references" / "2026-08-auth-spec.md").read_text(encoding="utf-8") == (
        "# Auth Spec\n\nBody.\n"
    )
    assert "wrote sources/2026-08-auth-spec.md" in result.stdout
    assert "wrote sources/references/2026-08-auth-spec.md" in result.stdout


def test_source_add_dry_run_writes_nothing(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = _add_source(root, _material(tmp_path), "--dry-run")

    assert result.exit_code == 0
    assert not (root / "sources").exists()
    assert "would create sources/2026-08-auth-spec.md" in result.stdout


def test_source_add_json_reports_both_writes(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = _add_source(root, _material(tmp_path), "--json", **{"--description": "d", "--origin": "o"})
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["applied"] is True
    assert payload["writes"] == [
        {"member": "sources/2026-08-auth-spec.md", "mode": "create"},
        {"member": "sources/references/2026-08-auth-spec.md", "mode": "create"},
    ]


def test_source_add_twice_refuses(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    material = _material(tmp_path)
    assert _add_source(root, material).exit_code == 0
    second = _add_source(root, material)

    assert second.exit_code == 1
    assert "target-exists" in second.stderr


def test_source_add_rejects_an_unknown_source_type(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = _add_source(root, _material(tmp_path), **{"--source-type": "blog"})

    assert result.exit_code == 1
    assert "--source-type 'blog'" in result.stderr
    assert "spec" in result.stderr


def test_source_add_rejects_binary_material(tmp_path) -> None:
    """S-D: the first cut is UTF-8 text only. A PDF is refused by name, not
    laundered into the bundle."""
    root = tmp_path / "b"
    _init(root)
    material = _material(tmp_path, name="scan.pdf", data=b"%PDF-1.4\n\xff\xfe\x00binary")
    result = _add_source(root, material)

    assert result.exit_code == 1
    assert "scan.pdf" in result.stderr
    assert "UTF-8" in result.stderr
    assert not (root / "sources").exists()


def test_source_add_rejects_a_missing_material(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = _add_source(root, tmp_path / "outside" / "nope.md")

    assert result.exit_code == 1
    assert "nope.md" in result.stderr


def test_source_add_carries_the_optional_frontmatter(tmp_path) -> None:
    root = tmp_path / "b"
    _init(root)
    result = runner.invoke(
        app,
        [
            "source",
            "add",
            str(root),
            str(_material(tmp_path)),
            "--title",
            "Auth Spec",
            "--description",
            "d",
            "--source-type",
            "spec",
            "--origin",
            "o",
            "--today",
            DAY,
            "--entity-uri",
            "pkg:o/r/auth",
            "--author",
            "Ada",
            "--author",
            "Grace",
            "--source-date",
            "2026-07-01",
            "--tokens",
            "1200",
        ],
    )
    assert result.exit_code == 0, result.output
    page = (root / "sources" / "2026-08-auth-spec.md").read_text(encoding="utf-8")
    assert "entity_uri: pkg:o/r/auth" in page
    assert "Ada" in page and "Grace" in page
    assert "tokens: 1200" in page


def test_the_ignore_list_covers_reference_copies() -> None:
    from doc_wiki_okf.cli import IGNORE

    assert IGNORE == (
        "_schema/*",
        "*/_schema/*",
        "_sections/*",
        "*/_sections/*",
        "sources/references/*",
        "*/sources/references/*",
    )
