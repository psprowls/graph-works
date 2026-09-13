from pathlib import Path

import pytest
from plugin_fork_io import Roots, Selection, Services, SourceSpec, plan_fork
from plugin_fork_io.previews import load_preview


def write_skill(root, name, body=b"Keep local intent.\n"):
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"---\nname: " + name.encode() + b"\ndescription: Example\n---\n" + body)
    return path


def selection(*names):
    return Selection.from_json(
        {
            "skills": [{"source": "skills/" + n, "name": n} for n in names],
            "resources": [],
            "dependencies": [],
            "adaptations": [],
            "source_links": [],
        }
    )


def test_renaming_changes_selected_invocation_but_keeps_attribution(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review", b"Use `$build`.\nOriginally from build.\nScratch /tmp/build stays.\n")
    write_skill(source, "build")
    chosen = Selection.from_json(
        {
            "skills": [
                {"source": "skills/review", "name": "local-review"},
                {"source": "skills/build", "name": "local-build"},
            ],
            "resources": [],
            "dependencies": [],
            "adaptations": [],
            "source_links": [],
        }
    )
    result = plan_fork(
        SourceSpec(str(source), "local"),
        chosen,
        Roots(tmp_path / "content", tmp_path / "state"),
        intent=("Keep distinct local commands",),
        services=Services.local(),
    )
    candidate = Path(result.data["candidate_path"])
    body = (candidate / "local-review/SKILL.md").read_bytes()
    assert b"$local-build" in body and b"Originally from build." in body
    assert b"/tmp/build" in body
    assert not (tmp_path / "content").exists()
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert preview.allowed
    assert not (tmp_path / "state" / "forks").exists()


@pytest.mark.parametrize("field,value", [("unknown", []), ("skills", [{"source": "../x", "name": "x"}])])
def test_selection_closed_schema(field, value):
    data = {"skills": [], "resources": [], "dependencies": [], "adaptations": [], "source_links": []}
    data[field] = value
    with pytest.raises(ValueError):
        Selection.from_json(data)


def prepare(tmp_path, chosen=None):
    return plan_fork(
        SourceSpec(str(tmp_path / "source"), "local"),
        chosen or selection("review"),
        Roots(tmp_path / "content", tmp_path / "state"),
        intent=(),
        services=Services.local(),
    )


def test_missing_required_helper_stages_without_selecting_it(tmp_path):
    write_skill(tmp_path / "source", "review", b"REQUIRED SUB-SKILL: build\n")
    write_skill(tmp_path / "source", "build")
    result = prepare(tmp_path)
    assert not result.allowed and result.preview_id
    assert result.data["unresolved"] == ["build"]
    assert not (Path(result.data["candidate_path"]) / "build").exists()


def test_optional_helper_does_not_block(tmp_path):
    write_skill(tmp_path / "source", "review", b"Optional: `$build`\n")
    assert prepare(tmp_path).allowed


def test_shared_escaped_resource_license_and_outside_invocation(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review", b"[Guide](../../shared/a\\(b\\).md)\n")
    write_skill(source, "outside", b"Use `$review`.\n")
    (source / "shared").mkdir()
    (source / "shared/a(b).md").write_bytes(b"Shared guide\n")
    (source / "LICENSE").write_bytes(b"Copyright original author\n")
    (source / "LICENSE.md").write_bytes(b"Original `$review` attribution\n")
    chosen = Selection.from_json(
        {
            "skills": [{"source": "skills/review", "name": "local-review"}],
            "resources": [{"source": "shared/a(b).md", "destination": "support/a(b).md"}],
            "dependencies": [],
            "adaptations": [],
            "source_links": [],
        }
    )
    result = prepare(tmp_path, chosen)
    assert result.allowed
    candidate = Path(result.data["candidate_path"])
    assert b"( <" not in (candidate / "local-review/SKILL.md").read_bytes()
    assert b"(<../support/a(b).md>)" in (candidate / "local-review/SKILL.md").read_bytes()
    assert (candidate / "LICENSE").read_bytes() == b"Copyright original author\n"
    assert (candidate / "LICENSE.md").read_bytes() == b"Original `$review` attribution\n"
    assert any(f.code == "reference.outside-selection" for f in result.findings)
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert "support/a(b).md" in preview.owned_paths and "LICENSE" in preview.owned_paths


def test_collision_refuses_before_staging(tmp_path):
    write_skill(tmp_path / "source", "review")
    write_skill(tmp_path / "source", "build")
    chosen = Selection.from_json(
        {
            "skills": [{"source": "skills/review", "name": "same"}, {"source": "skills/build", "name": "same"}],
            "resources": [],
            "dependencies": [],
            "adaptations": [],
            "source_links": [],
        }
    )
    result = prepare(tmp_path, chosen)
    assert not result.allowed and result.preview_id is None
    assert not (tmp_path / "state").exists()


def test_contained_link_materialization_keeps_original_evidence(tmp_path):
    from plugin_fork_io import read_snapshot

    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "shared").mkdir()
    (source / "shared/helper.txt").write_bytes(b"original helper")
    (source / "unrelated.txt").write_bytes(b"not selected")
    (source / "skills/review/helper.txt").symlink_to("../../shared/helper.txt")
    chosen = Selection.from_json(
        {
            "skills": [{"source": "skills/review", "name": "review"}],
            "resources": [],
            "dependencies": [],
            "adaptations": [],
            "source_links": [{"path": "skills/review/helper.txt", "action": "materialize"}],
        }
    )
    result = prepare(tmp_path, chosen)
    assert result.allowed
    candidate = Path(result.data["candidate_path"])
    assert (candidate / "review/helper.txt").read_bytes() == b"original helper"
    assert not (candidate / "review/helper.txt").is_symlink()
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert any(a.kind == "materialize-link" for a in preview.adaptations)
    base = read_snapshot(candidate.parent / "base.tar.gz")
    assert any(e.path == "skills/review/helper.txt" and e.kind == "symlink" for e in base.entries)
    assert not any(e.path == "unrelated.txt" for e in base.entries)


def test_immutable_candidate_tampering_refuses(tmp_path):
    write_skill(tmp_path / "source", "review")
    result = prepare(tmp_path)
    (Path(result.data["candidate_path"]) / "review/SKILL.md").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="candidate changed"):
        load_preview(tmp_path / "state", result.preview_id)


def test_host_requirement_is_inert_but_blocks(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    agents = source / "skills/review/agents"
    agents.mkdir()
    (agents / "openai.yaml").write_bytes(b"dependencies:\n  tools:\n    - type: mcp\n      value: example\n")
    result = prepare(tmp_path)
    assert not result.allowed and result.data["unresolved"] == ["example"]
    assert (Path(result.data["candidate_path"]) / "review/agents/openai.yaml").is_file()


def test_declared_supported_alternative_satisfies_group(tmp_path):
    write_skill(tmp_path / "source", "review")
    deps = [
        {
            "kind": "alternative",
            "target": target,
            "path": "skills/review/SKILL.md",
            "line": 5,
            "start": 0,
            "end": 0,
            "evidence": "user",
            "required": True,
            "group": "runner",
            "satisfied": supplied,
        }
        for target, supplied in [("tool-a", False), ("tool-b", True)]
    ]
    chosen = Selection.from_json(
        {
            "skills": [{"source": "skills/review", "name": "review"}],
            "resources": [],
            "dependencies": deps,
            "adaptations": [],
            "source_links": [],
        }
    )
    result = prepare(tmp_path, chosen)
    assert result.allowed
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert all(d.evidence == "user" and d.satisfied for d in preview.dependencies)


def test_declared_host_supply_satisfies_scanned_requirement(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "skills/review/agents").mkdir()
    (source / "skills/review/agents/openai.yaml").write_bytes(b"dependencies:\n  tools:\n    - value: example\n")
    dependency = {
        "kind": "host",
        "target": "example",
        "path": "skills/review/agents/openai.yaml",
        "line": 1,
        "start": 0,
        "end": 0,
        "evidence": "user",
        "required": True,
        "group": None,
        "satisfied": True,
    }
    chosen = Selection.from_json(
        {
            "skills": [{"source": "skills/review", "name": "review"}],
            "resources": [],
            "dependencies": [dependency],
            "adaptations": [],
            "source_links": [],
        }
    )
    assert prepare(tmp_path, chosen).allowed


def test_discovery_context_reports_collision_and_limits(tmp_path):
    from plugin_fork_io import DiscoveryContext

    source = tmp_path / "source"
    write_skill(source, "review")
    discovery = tmp_path / "discovery"
    write_skill(discovery, "review")
    result = plan_fork(
        SourceSpec(str(source), "local"),
        selection("review"),
        Roots(tmp_path / "content", tmp_path / "state"),
        intent=(),
        services=Services.local(),
        discovery=DiscoveryContext((discovery, tmp_path / "missing"), ("Admin sources unavailable",)),
    )
    assert not result.allowed
    assert {f.code for f in result.findings} >= {"discovery.collision", "discovery.limit"}


def test_fork_cli_prepares_and_invalid_selection_has_structured_exit(tmp_path):
    import json

    from plugin_fork_io.cli import app
    from typer.testing import CliRunner

    source = tmp_path / "source"
    write_skill(source, "review")
    config = tmp_path / "selection.json"
    config.write_bytes(
        json.dumps(
            {
                "skills": [{"source": "skills/review", "name": "review"}],
                "resources": [],
                "dependencies": [],
                "adaptations": [],
                "source_links": [],
            }
        ).encode()
    )
    runner = CliRunner()
    result = runner.invoke(app, ["fork", str(source), "--selection", str(config), "--project", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["preview_id"]
    config.write_bytes(b'{"unknown":true}')
    result = runner.invoke(app, ["fork", str(source), "--selection", str(config), "--json"])
    assert result.exit_code == 2 and json.loads(result.output)["operation"] == "fork"


def test_duplicate_reference_destination_edits_are_deduplicated(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review", b"[One][ref] and [Two][ref]\n\n[ref]: helper.md\n")
    (source / "skills/review/helper.md").write_bytes(b"Helper\n")
    assert prepare(tmp_path).allowed


def test_update_candidate_is_editable_but_evidence_is_immutable(tmp_path):
    from plugin_fork_io import apply_preview, plan_update

    write_skill(tmp_path / "source", "review")
    initial = prepare(tmp_path)
    assert apply_preview(tmp_path / "state", initial.preview_id, services=Services.local()).applied
    result = plan_update(
        Roots(tmp_path / "content", tmp_path / "state"),
        initial.variant_id,
        SourceSpec(str(tmp_path / "source"), "local"),
        selection=None,
        mappings={},
        services=Services.local(),
    )
    preview = load_preview(tmp_path / "state", result.preview_id)
    stage = Path(preview.candidate_path).parent
    (Path(preview.candidate_path) / "review/SKILL.md").write_bytes(b"Authorized candidate edits")
    assert load_preview(tmp_path / "state", result.preview_id).operation == "update"
    (stage / "base.tar.gz").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact changed"):
        load_preview(tmp_path / "state", result.preview_id)


def test_owned_observation_excludes_unrelated_siblings(tmp_path):
    write_skill(tmp_path / "source", "review")
    owned = tmp_path / "content/review"
    owned.mkdir(parents=True)
    (owned / "extra.txt").write_bytes(b"local changes")
    (tmp_path / "content/unrelated.txt").write_bytes(b"other owner")
    result = prepare(tmp_path)
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert not result.allowed
    paths = {entry.path for entry in preview.observed[0].entries}
    assert "review/extra.txt" in paths and "unrelated.txt" not in paths


def test_explicit_permission_adaptation_preserves_original_mode(tmp_path):
    from plugin_fork_io import read_snapshot

    source = tmp_path / "source"
    path = write_skill(source, "review")
    path.chmod(0o644)
    chosen = Selection.from_json(
        {
            "skills": [{"source": "skills/review", "name": "review"}],
            "resources": [],
            "dependencies": [],
            "source_links": [],
            "adaptations": [
                {
                    "kind": "permission",
                    "path": "skills/review/SKILL.md",
                    "start": 0,
                    "end": 4,
                    "expected": b"0644".hex(),
                    "replacement": b"0600".hex(),
                    "purpose": "Private local file",
                }
            ],
        }
    )
    result = prepare(tmp_path, chosen)
    assert result.allowed
    candidate = Path(result.data["candidate_path"])
    assert (candidate / "review/SKILL.md").stat().st_mode & 0o777 == 0o600
    base = read_snapshot(candidate.parent / "base.tar.gz")
    assert next(e.mode for e in base.entries if e.path.endswith("SKILL.md")) == 0o644


@pytest.mark.parametrize(
    "body",
    [
        b"> Use [missing](missing.md)\n> before continuing.\n",
        b"> Use `$missing`\n> before continuing.\n",
        b"Nonoptional: `$missing`\n",
        b"Not optional: `$missing`\n",
        b"Optional: `$helper`; required: `$missing`\n",
    ],
)
def test_required_dependencies_cannot_disappear_or_become_optional(tmp_path, body):
    write_skill(tmp_path / "source", "review", body)
    result = prepare(tmp_path)
    assert not result.allowed and result.preview_id
    assert any("missing" in target for target in result.data["unresolved"])


def test_unlocated_required_rename_blocks_but_stages(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.records import Component

    write_skill(tmp_path / "source", "review", b"> Use `$review`\n> before continuing.\n")
    chosen = replace(selection("review"), skills=(Component("skills/review", "local-review"),))
    result = prepare(tmp_path, chosen)
    assert not result.allowed and result.preview_id
    assert any(f.code == "adaptation.ambiguous" for f in result.findings)


def explicit_edit(path, before, after, *, kind="invocation"):
    from plugin_fork_io.records import Adaptation

    content = path.read_bytes()
    start = content.index(before)
    return Adaptation(kind, "skills/review/SKILL.md", start, start + len(before), before, after, "Reviewed edit")


def test_final_candidate_rechecks_new_required_invocations(tmp_path):
    from dataclasses import replace

    path = write_skill(tmp_path / "source", "review", b"Use `$review`.\n")
    chosen = replace(selection("review"), adaptations=(explicit_edit(path, b"$review", b"$missing"),))
    result = prepare(tmp_path, chosen)
    assert not result.allowed and result.data["unresolved"] == ["missing"]
    assert any(f.path == "review/SKILL.md" and f.code == "dependency.missing" for f in result.findings)


def test_approved_repair_clears_original_missing_requirement(tmp_path):
    from dataclasses import replace

    path = write_skill(tmp_path / "source", "review", b"Use `$missing`.\n")
    chosen = replace(selection("review"), adaptations=(explicit_edit(path, b"$missing", b"$review"),))
    result = prepare(tmp_path, chosen)
    assert result.allowed and result.data["unresolved"] == []
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert any(d.target == "missing" for d in preview.dependencies)


@pytest.mark.parametrize(
    "before,after", [(b"name: review", b"name: wrong"), (b"description: Example", b"description: ''")]
)
def test_final_candidate_rechecks_metadata_and_folder_name(tmp_path, before, after):
    from dataclasses import replace

    path = write_skill(tmp_path / "source", "review")
    chosen = replace(selection("review"), adaptations=(explicit_edit(path, before, after, kind="skill-name"),))
    result = prepare(tmp_path, chosen)
    assert not result.allowed and result.preview_id
    assert any(f.path == "review/SKILL.md" and f.code.startswith("skill.") for f in result.findings)


def test_final_candidate_rechecks_changed_resource_destination(tmp_path):
    from dataclasses import replace

    path = write_skill(tmp_path / "source", "review", b"[Helper](helper.md)\n")
    (path.parent / "helper.md").write_bytes(b"Helper\n")
    chosen = replace(
        selection("review"), adaptations=(explicit_edit(path, b"helper.md", b"missing.md", kind="resource-path"),)
    )
    result = prepare(tmp_path, chosen)
    assert not result.allowed and result.data["unresolved"] == ["review/missing.md"]


def test_approved_resource_repair_uses_final_candidate_requirements(tmp_path):
    from dataclasses import replace

    path = write_skill(tmp_path / "source", "review", b"[Helper](missing.md)\n")
    (path.parent / "helper.md").write_bytes(b"Helper\n")
    chosen = replace(
        selection("review"), adaptations=(explicit_edit(path, b"missing.md", b"helper.md", kind="resource-path"),)
    )
    result = prepare(tmp_path, chosen)
    assert result.allowed and result.data["unresolved"] == []
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert any(d.target == "skills/review/missing.md" for d in preview.dependencies)
    assert any(d.target == "review/helper.md" and d.satisfied for d in preview.candidate_dependencies)


def test_outside_scan_retains_per_file_limits(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "outside.md").write_bytes(b"\xff")
    result = prepare(tmp_path)
    assert result.allowed
    assert any(f.code == "discovery.limit" and f.path == "outside.md" for f in result.findings)


def test_repeated_required_invocation_after_emphasis_blocks_preview(tmp_path):
    write_skill(tmp_path / "source", "review", b"Optional: $missing **then required:** $missing\n")
    result = prepare(tmp_path)
    assert not result.allowed and result.preview_id
    assert result.data["unresolved"] == ["missing"]
    preview = load_preview(tmp_path / "state", result.preview_id)
    assert [(d.target, d.required) for d in preview.candidate_dependencies] == [("missing", False), ("missing", True)]
