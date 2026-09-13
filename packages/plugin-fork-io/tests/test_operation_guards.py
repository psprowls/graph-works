"""Public operations refuse malformed ownership and original-evidence claims."""

from dataclasses import replace
from pathlib import Path

import pytest
from helpers import selection, write_skill
from plugin_fork_io import Roots, Services, SourceSpec, plan_adopt
from plugin_fork_io.snapshots import capture


@pytest.mark.parametrize(
    "case",
    [
        "relative",
        "nested",
        "empty",
        "adaptation",
        "link",
        "overlap",
        "missing-skill",
        "bad-metadata",
        "missing-resource",
        "evidence-without-base",
        "unknown-evidence",
        "blank-declaration",
        "missing-mappings",
        "wrong-mappings",
        "overlap-mappings",
        "wrong-hash",
        "wrong-revision",
        "missing-source",
    ],
)
def test_adoption_refuses_invalid_roots_selection_and_original_claims(tmp_path, case):
    from plugin_fork_io.records import Adaptation, Component, SourceLink, SourceMapping

    roots = Roots(tmp_path / "content", tmp_path / "state")
    skill = write_skill(roots.content, "review")
    source = tmp_path / "original"
    write_skill(source, "review")
    selected = selection("review")
    base = SourceSpec(str(source), "local")
    evidence = {
        "digest": capture(base, (), services=Services.local()).digest,
        "declaration": "Captured original",
        "mappings": [{"source": "skills/review", "destination": "skills/review"}],
    }
    if case == "relative":
        roots = Roots(Path("relative"), roots.state)
    elif case == "nested":
        roots = Roots(roots.content, roots.content / "state")
    elif case == "empty":
        selected = replace(selected, skills=())
    elif case == "adaptation":
        selected = replace(
            selected, adaptations=(Adaptation("invocation", "skills/review/SKILL.md", 0, 1, b"a", b"b", "test"),)
        )
    elif case == "link":
        selected = replace(selected, source_links=(SourceLink("skills/review/link", "materialize"),))
    elif case == "overlap":
        selected = replace(selected, resources=(SourceMapping("x", "skills/review/child"),))
    elif case == "missing-skill":
        skill.unlink()
    elif case == "bad-metadata":
        skill.write_bytes(b"not a skill")
    elif case == "missing-resource":
        selected = replace(selected, resources=(SourceMapping("x", "absent"),))
    elif case == "evidence-without-base":
        base = None
    elif case == "unknown-evidence":
        evidence["invented"] = True
    elif case == "blank-declaration":
        evidence["declaration"] = " "
    elif case == "missing-mappings":
        del evidence["mappings"]
    elif case == "wrong-mappings":
        evidence["mappings"] = []
    elif case == "overlap-mappings":
        write_skill(roots.content, "second")
        selected = replace(selected, skills=(*selected.skills, Component("skills/second", "second")))
        evidence["mappings"] = [
            {"source": "skills/review", "destination": "skills/review"},
            {"source": "skills/review/child", "destination": "skills/second"},
        ]
    elif case == "wrong-hash":
        evidence["digest"] = "0" * 64
    elif case == "wrong-revision":
        evidence["revision"] = "not-the-original"
    elif case == "missing-source":
        evidence["mappings"][0]["source"] = "absent"
    result = plan_adopt(roots, selected, base=base, evidence=evidence, services=Services.local())
    assert not result.allowed and not result.applied
    assert result.findings and result.preview_id is None
    assert not roots.state.exists()


@pytest.mark.parametrize(
    "case",
    [
        "other-variant",
        "other-root",
        "fork-preview",
        "missing-preview",
        "missing-ledger",
        "corrupt-history",
        "unknown-resolution",
        "stale-review",
        "write-failure",
    ],
)
def test_acceptance_refuses_wrong_authority_and_stale_evidence(tmp_path, case):
    from plugin_fork_io import Resolution, Review, load_preview
    from plugin_fork_io.acceptance import plan_accept
    from test_acceptance import prepared

    roots, variant, update = prepared(tmp_path)
    candidate = update.preview_id
    review = None
    resolutions = ()
    services = Services.local()
    if case == "other-variant":
        variant = "another"
    elif case == "other-root":
        roots = Roots(tmp_path / "other", roots.state)
    elif case == "fork-preview":
        candidate = next(
            p.parent.name
            for p in (roots.state / "previews").glob("*/preview.json")
            if load_preview(roots.state, p.parent.name).operation == "fork"
        )
    elif case == "missing-preview":
        candidate = "missing"
    elif case == "missing-ledger":
        (roots.state / "forks" / variant / "ledger.json").unlink()
    elif case == "corrupt-history":
        history = next((roots.state / "forks" / variant / "history").glob("*.json"))
        history.write_bytes(b"{}")
    elif case == "unknown-resolution":
        resolutions = (Resolution("0" * 64, "local-review/SKILL.md", "0" * 64),)
    elif case == "stale-review":
        review = Review("0" * 64, ())
    elif case == "write-failure":
        filesystem = services.filesystem

        class Broken:
            def __getattr__(self, name):
                return getattr(filesystem, name)

            def write_exclusive(self, *args, **kwargs):
                raise OSError("write refused")

        services = replace(services, filesystem=Broken())
    result = plan_accept(
        roots, variant, candidate, review=review, intent=(), resolutions=resolutions, services=services
    )
    assert not result.allowed and not result.applied
    assert result.findings


@pytest.mark.parametrize(
    "case",
    [
        "empty-agents",
        "unknown-agent",
        "scope",
        "mode",
        "relative-root",
        "relative-project",
        "relative-home",
        "relative-destination",
        "bad-history",
        "duplicate-root",
    ],
)
def test_installation_refuses_unsupported_context_and_duplicate_writable_root(tmp_path, case):
    import shutil

    from test_installation import install
    from test_updates import forked

    roots, variant = forked(tmp_path)
    args = {}
    if case == "empty-agents":
        args["agents"] = ()
    elif case == "unknown-agent":
        args["agents"] = ("arbitrary",)
    elif case == "scope":
        args["scope"] = "global"
    elif case == "mode":
        args["mode"] = "fallback"
    elif case == "relative-root":
        roots = Roots(Path("relative"), roots.state)
    elif case == "relative-project":
        args["project"] = Path("relative")
    elif case == "relative-home":
        args["home"] = Path("relative")
    elif case == "relative-destination":
        args["destination"] = Path("relative")
    elif case == "bad-history":
        next((roots.state / "forks" / variant / "history").glob("*.json")).write_bytes(b"{}")
    elif case == "duplicate-root":
        copied = tmp_path / "duplicate"
        shutil.copytree(roots.content, copied)
        roots = Roots(copied, roots.state)
    result = install(roots, variant, tmp_path, **args)
    assert not result.allowed and not result.applied
    assert not (tmp_path / "client").exists()


@pytest.mark.parametrize(
    "case", ["relative", "unowned-mapping", "missing-source-map", "missing-resource", "missing-edit", "missing-link"]
)
def test_update_refuses_invalid_alignment_and_unselected_approvals(tmp_path, case):
    from plugin_fork_io import load_ledger
    from plugin_fork_io.records import Adaptation, Selection, SourceLink, SourceMapping
    from plugin_fork_io.updates import plan_update
    from test_updates import forked

    roots, variant = forked(tmp_path)
    ledger = load_ledger(roots.state, variant)
    chosen = Selection(ledger.components, (), (), (), ())
    mappings = {}
    if case == "relative":
        roots = Roots(Path("relative"), roots.state)
    elif case == "unowned-mapping":
        mappings = {"outside": "new"}
    elif case == "missing-source-map":
        mappings = {"skills/review/missing": "skills/review/SKILL.md"}
    elif case == "missing-resource":
        chosen = replace(chosen, resources=(SourceMapping("missing", "new"),))
    elif case == "missing-edit":
        chosen = replace(chosen, adaptations=(Adaptation("invocation", "missing", 0, 1, b"a", b"b", "test"),))
    elif case == "missing-link":
        chosen = replace(chosen, source_links=(SourceLink("missing", "materialize"),))
    result = plan_update(
        roots,
        variant,
        SourceSpec(str(tmp_path / "source"), "local"),
        selection=chosen,
        mappings=mappings,
        services=Services.local(),
    )
    assert not result.allowed and not result.applied and result.preview_id is None


@pytest.mark.parametrize(
    "case",
    [
        "relative",
        "same-root",
        "empty-selection",
        "forged-evidence",
        "missing-selected",
        "duplicate-source",
        "missing-definition",
        "duplicate-name",
        "fake-materialization",
        "approval-outside-selection",
    ],
)
def test_fork_refuses_invalid_source_ownership_and_approval_claims(tmp_path, case):
    from plugin_fork_io import plan_fork
    from plugin_fork_io.records import Adaptation, Component, Dependency, SourceLink, SourceMapping

    source = tmp_path / "source"
    definition = write_skill(source, "review")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    chosen = selection("review")
    if case == "relative":
        roots = Roots(Path("relative"), roots.state)
    elif case == "same-root":
        roots = Roots(source, source)
    elif case == "empty-selection":
        chosen = replace(chosen, skills=())
    elif case == "forged-evidence":
        chosen = replace(chosen, dependencies=(Dependency("host", "tool", "x", 1, 0, 0, "manifest"),))
    elif case == "missing-selected":
        chosen = replace(chosen, resources=(SourceMapping("absent", "absent"),))
    elif case == "duplicate-source":
        chosen = replace(chosen, resources=(SourceMapping("skills/review", "copy"),))
    elif case == "missing-definition":
        definition.unlink()
    elif case == "duplicate-name":
        second = write_skill(source, "second")
        second.write_bytes(definition.read_bytes())
        chosen = replace(chosen, skills=(*chosen.skills, Component("skills/second", "second")))
    elif case == "fake-materialization":
        chosen = replace(
            chosen, adaptations=(Adaptation("materialize-link", "skills/review/link", 0, 1, b"a", b"", "Claimed"),)
        )
    elif case == "approval-outside-selection":
        chosen = replace(chosen, source_links=(SourceLink("other", "materialize"),))
    result = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())
    assert not result.allowed and not result.applied


@pytest.mark.parametrize(
    "content",
    [
        b"[]",
        b"dependencies: []",
        b"dependencies: {tools: 7}",
        b"dependencies: {tools: [7]}",
        b"dependencies: {tools: [{value: 7}]}",
        b"\xff",
    ],
)
def test_malformed_host_requirements_remain_a_located_blocker(content):
    from plugin_fork_io.references import scan_host_metadata

    dependencies, findings = scan_host_metadata(content, "agents/openai.yaml")
    assert not dependencies
    assert [(f.code, f.severity, f.path, f.line) for f in findings] == [
        ("dependency.metadata", "error", "agents/openai.yaml", 1)
    ]


@pytest.mark.parametrize(
    "content",
    [
        b"---\n[]\n---\n",
        b"---\nname: [review]\n---\n",
        b"---\nname: &anchor review\n---\n",
        b"---\nname: >\n  review\n---\n",
        b"---\nname: review\n",
        b"\xff",
    ],
)
def test_ambiguous_name_rewrite_preserves_bytes_and_requires_explicit_edit(content):
    from plugin_fork_io.adaptations import name_adaptation

    edit, findings = name_adaptation(content, "SKILL.md", "local-review")
    assert edit is None and findings[0].code == "adaptation.ambiguous"


@pytest.mark.parametrize(
    "case", ["relative-config", "blank-path", "unknown-default", "non-array-projects", "unknown-project"]
)
def test_configuration_refuses_ambiguous_or_malformed_storage_locations(tmp_path, case):
    from plugin_fork_io.config import ConfigError, resolve_settings

    path = tmp_path / "config.json"
    config = {"schema_version": 1}
    if case == "relative-config":
        path = Path("config.json")
    elif case == "blank-path":
        config["defaults"] = {"state_dir": " "}
    elif case == "unknown-default":
        config["defaults"] = {"registry": "elsewhere"}
    elif case == "non-array-projects":
        config["projects"] = {}
    else:
        config["projects"] = [{"project": str(tmp_path), "unexpected": True}]
    with pytest.raises(ConfigError):
        resolve_settings({"project": tmp_path}, config, path, home=tmp_path, platform="linux", environment={})
    assert not path.exists()
