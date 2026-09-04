"""The six acts of workspace init, and the vocabulary they report in."""

from __future__ import annotations

import re
from datetime import date

import code_wiki_okf.init
import doc_wiki_okf.init
import pytest
import work_tracker_okf.init
from graph_works_core.workspace.errors import InitError
from graph_works_core.workspace.init import INSTALLERS, apply_init, plan_init

TODAY = date(2026, 8, 13)


def _init(root, **kwargs):
    return apply_init(plan_init(root, today=TODAY, **kwargs))


# --- the installer roster ---------------------------------------------------


def test_the_shipped_installers_are_the_three_packages_that_have_one():
    assert (
        code_wiki_okf.init.install_bundle,
        work_tracker_okf.init.install_bundle,
        doc_wiki_okf.init.install_bundle,
    ) == INSTALLERS


def test_all_three_installers_land_without_a_foreign_content_refusal(tmp_path):
    """The pairing the item's own plan table calls for: a fresh init over all
    three installers reports `ok` and both packages' fragments resolve."""
    from okf_ext.shape.loader import load_sections

    result = _init(tmp_path / "works")
    assert result.ok

    sections = load_sections(result.layout.config_dir / "sections")
    assert sections.fragments["see_also"]
    assert sections.fragments["plan_table"]


# --- planning writes nothing ------------------------------------------------


def test_planning_touches_no_disk(tmp_path):
    root = tmp_path / "works"
    plan = plan_init(root, today=TODAY)
    assert not root.exists()
    assert not plan.is_empty
    assert plan.ok


def test_a_root_that_is_a_file_is_caller_error(tmp_path):
    path = tmp_path / "works"
    path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(InitError):
        plan_init(path, today=TODAY)


def test_plan_init_refuses_without_hard_link_support(tmp_path, monkeypatch):
    from graph_works_core.workspace import anchors

    monkeypatch.setattr(anchors, "hard_links_supported", lambda path: False)
    root = tmp_path / "works"
    with pytest.raises(InitError) as caught:
        plan_init(root, today=TODAY)
    assert str(caught.value) == anchors._hard_link_refusal_message(root.resolve())


def test_plan_init_creates_nothing_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    from graph_works_core.workspace import anchors

    monkeypatch.setattr(anchors, "hard_links_supported", lambda path: False)
    root = tmp_path / "works"
    with pytest.raises(InitError):
        plan_init(root, today=TODAY)
    assert not root.exists()


# --- act 1: directories -----------------------------------------------------


def test_apply_creates_every_layout_directory(tmp_path):
    result = _init(tmp_path / "works")
    for directory in result.layout.directories:
        assert directory.is_dir(), directory


# --- act 2: the workspace's own gitignore, nested under .gw/ ----------------


def test_the_gitignore_holds_the_two_gitignored_members(tmp_path):
    result = _init(tmp_path / "works")
    text = (result.layout.config_dir / ".gitignore").read_text(encoding="utf-8")
    assert "/cache/" in text
    assert "/worktrees/" in text


def test_the_repo_gitignore_is_never_edited(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    repo_gitignore = repo / ".gitignore"
    repo_gitignore.write_text("*.pyc\n", encoding="utf-8")
    _init(repo / ".works")
    assert repo_gitignore.read_text(encoding="utf-8") == "*.pyc\n"


def test_a_hand_written_gitignore_gains_only_the_missing_entry(tmp_path):
    root = tmp_path / "works"
    root.mkdir()
    gw = root / ".gw"
    gw.mkdir()
    (gw / ".gitignore").write_text("/cache/\n*.tmp", encoding="utf-8")
    _init(root)
    text = (gw / ".gitignore").read_text(encoding="utf-8")
    assert text.count("/cache/") == 1
    assert "*.tmp" in text
    assert "/worktrees/" in text


def test_a_workspace_with_no_gitignorable_members_writes_no_gitignore(tmp_path):
    root = tmp_path / "works"
    root.mkdir()
    (root / "workspace.yaml").write_text(
        f'version: 1\nlayout:\n  cache_dir: "{tmp_path / "c"}"\n  worktrees_dir: "{tmp_path / "w"}"\n',
        encoding="utf-8",
    )
    apply_init(plan_init(root, today=TODAY))
    assert not (root / ".gw" / ".gitignore").exists()


# --- the ticket's own acceptance: everything collapses into .gw/ ------------


def test_a_fresh_bootstrap_produces_exactly_the_gw_tree(tmp_path):
    """`<root>/{workspace.yaml, .gw/, okf/}`, with `.gw/` holding the config
    dir's own members -- cache, worktrees, gitignore, and the installers'
    declarations. `repositories:`/`ignore:` live inside `workspace.yaml`
    itself -- no other file carries them."""
    result = _init(tmp_path / "works")
    root = result.layout.root

    assert {path.name for path in root.iterdir()} == {"workspace.yaml", ".gw", "okf", "CLAUDE.md", "AGENTS.md"}
    gw_members = {path.name for path in (root / ".gw").iterdir()}
    assert {"cache", "worktrees", ".gitignore"} <= gw_members
    assert not (root / ".gw" / "_repositories.yaml").exists()
    assert not (root / "okf" / "_repositories.yaml").exists()
    assert "repositories:" in (root / "workspace.yaml").read_text(encoding="utf-8")


# --- act 3: the manifest ----------------------------------------------------


def test_the_manifest_is_written_once_and_never_overwritten(tmp_path):
    root = tmp_path / "works"
    _init(root, topic="My Works")
    before = (root / "workspace.yaml").read_text(encoding="utf-8")
    assert "My Works" in before
    _init(root, topic="A Different Topic")
    assert (root / "workspace.yaml").read_text(encoding="utf-8") == before


# --- act 7: the config catalog's projection ---------------------------------


def test_gw_init_writes_the_config_projection(tmp_path):
    plan = plan_init(tmp_path / "works", today=TODAY)
    result = apply_init(plan)
    projection = plan.layout.cache_dir / "config.json"
    assert projection.is_file()
    assert result.changed


def test_a_second_init_does_not_report_changed_for_an_unchanged_projection(tmp_path):
    root = tmp_path / "works"
    apply_init(plan_init(root, today=TODAY))
    second_result = apply_init(plan_init(root, today=TODAY))
    assert not second_result.changed


# --- act 4: the bundle scaffold, and its declaration routing ----------------


def test_the_scaffold_routes_declarations_and_leaves_the_rest_in_the_bundle(tmp_path):
    result = _init(tmp_path / "works")
    layout = result.layout
    assert (layout.bundle_dir / "index.md").is_file()
    assert (layout.bundle_dir / "log.md").is_file()
    assert (layout.config_dir / "tags.yaml").is_file()
    assert not (layout.bundle_dir / "tags.yaml").exists()


# --- act 5: the installers --------------------------------------------------


def test_both_installers_land_their_declarations_under_the_config_dir(tmp_path):
    result = _init(tmp_path / "works")
    layout = result.layout
    assert (layout.config_dir / "schema" / "Package.schema.json").is_file()
    assert (layout.config_dir / "sections" / "Package.yaml").is_file()
    assert (layout.config_dir / "schema" / "Feature.schema.json").is_file()
    assert (layout.config_dir / "sections" / "Feature.yaml").is_file()
    assert result.ok


def test_the_installer_roster_is_injectable(tmp_path):
    result = _init(tmp_path / "works", installers=())
    assert result.installs == ()
    assert (result.layout.bundle_dir / "index.md").is_file()
    assert not (result.layout.config_dir / "schema").exists()


# --- act 3: repositories/ignore, seeded from the layout into workspace.yaml -


def test_a_declared_repository_round_trips_through_load_config(tmp_path):
    from code_wiki_okf.config import load_config

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    result = _init(repo / ".works")
    config = load_config(
        result.layout.bundle_dir,
        config_path=result.layout.manifest_path,
        graph_dir=result.layout.cache_dir,
        declarations_dir=result.layout.config_dir,
    )
    assert config.graph_dir == result.layout.cache_dir
    assert config.declarations_dir == result.layout.config_dir
    assert [entry.name for entry in config.repos] == [repo.name]
    assert config.repos[0].path == repo.resolve()
    assert config.repos[0].ignore == (".works/**",)


def test_declared_repository_path_climbs_from_workspace_root_not_bundle_dir(tmp_path):
    """The written `repositories.<name>.path` is relative to `layout.root`
    (where `workspace.yaml` lives) -- one `..` segment here, not two, since
    `layout.bundle_dir` sits one level inside `layout.root`."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    result = _init(repo / ".works")

    raw = result.layout.manifest_path.read_text(encoding="utf-8")
    match = re.search(r'path:\s*"?([^"\n]+)"?', raw)
    assert match is not None
    assert match.group(1) == ".."


def test_a_workspace_outside_a_repo_declares_no_repository(tmp_path):
    from code_wiki_okf.config import load_config

    result = _init(tmp_path / "works")
    config = load_config(
        result.layout.bundle_dir,
        config_path=result.layout.manifest_path,
        graph_dir=result.layout.cache_dir,
        declarations_dir=result.layout.config_dir,
    )
    assert config.repos == ()
    assert config.graph_dir == result.layout.cache_dir


def test_an_explicit_repo_root_overrides_detection(tmp_path):
    from code_wiki_okf.config import load_config

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _init(tmp_path / "works", repo_root=elsewhere)
    assert result.layout.repo_root == elsewhere.resolve()
    config = load_config(
        result.layout.bundle_dir,
        config_path=result.layout.manifest_path,
        graph_dir=result.layout.cache_dir,
        declarations_dir=result.layout.config_dir,
    )
    assert [entry.name for entry in config.repos] == ["elsewhere"]


def test_a_stray_repositories_yaml_is_inert_dead_weight(tmp_path):
    """A `.gw/_repositories.yaml` file is not read or written by init. If one
    exists on disk, init leaves it untouched and does not let its content
    influence `workspace.yaml`."""
    root = tmp_path / "works"
    root.mkdir()
    gw = root / ".gw"
    gw.mkdir()
    stray = gw / "_repositories.yaml"
    edited = "repositories:\n  hand-edited:\n    path: /nowhere\n"
    stray.write_text(edited, encoding="utf-8")
    _init(root)
    assert stray.read_text(encoding="utf-8") == edited
    assert "hand-edited" not in (root / "workspace.yaml").read_text(encoding="utf-8")


# --- act 7: CLAUDE.md / AGENTS.md, seeding the prompt layer's project context ---


def test_a_fresh_init_writes_both_context_files_with_identical_bodies(tmp_path):
    result = _init(tmp_path / "works")
    claude = (result.layout.root / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (result.layout.root / "AGENTS.md").read_text(encoding="utf-8")
    assert claude == agents
    assert "## Style" in claude
    assert "## Log format" in claude


def test_context_files_land_in_the_repo_the_workspace_lives_in(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    result = _init(repo / ".works")
    assert (repo / "CLAUDE.md").is_file()
    assert (repo / "AGENTS.md").is_file()
    assert not (result.layout.root / "CLAUDE.md").exists()


def test_a_hand_edited_context_file_keeps_its_prose_on_reinit(tmp_path):
    root = tmp_path / "works"
    _init(root)
    path = root / "CLAUDE.md"
    edited = path.read_text(encoding="utf-8") + "\n## Extra\n\nTeam-specific note.\n"
    path.write_text(edited, encoding="utf-8")
    _init(root)
    assert "Team-specific note." in path.read_text(encoding="utf-8")


def test_render_project_context_reads_back_a_freshly_bootstrapped_workspace(tmp_path):
    from graph_works_core.prompts.project_context import render_project_context

    result = _init(tmp_path / "works")
    rendered = render_project_context(result.layout)
    assert rendered != ""
    assert "§Style" in rendered
    assert "§Log format" in rendered


# --- idempotence ------------------------------------------------------------


def test_a_second_plan_over_an_initialized_workspace_is_empty(tmp_path):
    root = tmp_path / "works"
    _init(root)
    assert plan_init(root, today=TODAY).is_empty


def test_a_second_apply_reports_no_change(tmp_path):
    root = tmp_path / "works"
    _init(root)
    assert not _init(root).changed


def test_a_first_apply_reports_change_and_renders_a_diff(tmp_path):
    result = _init(tmp_path / "works")
    assert result.changed
    rendered = result.diff()
    assert "workspace.yaml" in rendered
    assert ".gitignore" in rendered


def test_a_plan_over_a_customized_workspace_previews_that_workspace(tmp_path):
    root = tmp_path / "works"
    root.mkdir()
    (root / "workspace.yaml").write_text("version: 1\nlayout:\n  bundle_dir: wiki\n", encoding="utf-8")
    apply_init(plan_init(root, today=TODAY))
    assert (root / "wiki" / "index.md").is_file()
    assert not (root / "okf").exists()


# --- the seeded relay tail --------------------------------------------------


def test_a_fresh_workspace_is_born_with_a_relay_tail(tmp_path):
    from graph_works_core.workspace.pipeline import RELAY_TAIL_SEED, pipeline_table

    result = _init(tmp_path / "works")
    text = (result.layout.root / "workspace.yaml").read_text(encoding="utf-8")
    assert "prompt_tail" in text
    # The round trip, not just the write: this is what proves the seeded string
    # is a value the catalog accepts and `_prompt` will substitute.
    assert pipeline_table(layout=result.layout)["branch"].prompt_tail == RELAY_TAIL_SEED


def test_the_seeded_tail_carries_the_relay_trigger(tmp_path):
    result = _init(tmp_path / "works")
    from graph_works_core.workspace.pipeline import pipeline_table

    tail = pipeline_table(layout=result.layout)["branch"].prompt_tail
    assert tail is not None
    assert tail.startswith("Auto-drive context:")
    assert "{merge_target}" in tail


# --- the plan's own renderer -------------------------------------------------


def test_a_fresh_plan_renders_every_act_it_will_perform(tmp_path):
    root = tmp_path / "works"

    lines = plan_init(root, today=TODAY, topic="Demo").diff().splitlines()

    assert f"+ {root.resolve()}/" in lines
    assert "+ .gw/.gitignore" in lines
    assert "+ workspace.yaml" in lines
    assert "+ index.md" in lines
    assert "+ schema/Package.schema.json" in lines


def test_a_fresh_plan_never_mentions_repositories_yaml(tmp_path):
    """`repositories:`/`ignore:` are rendered straight into act 3's own
    `workspace.yaml` write, so no line in the plan -- not act 3's, not any
    installer's -- names `_repositories.yaml`."""
    lines = plan_init(tmp_path / "works", today=TODAY).diff().splitlines()

    assert not any("_repositories.yaml" in line for line in lines)


def test_a_second_plan_renders_no_additions(tmp_path):
    """Idempotence, in the renderer's vocabulary.

    Not "empty": the three scaffold members come back as `already-present` skips,
    exactly as they do from `WorkspaceInit.diff()` on a second apply. The property
    that matters is that nothing is *added*.
    """
    root = tmp_path / "works"
    _init(root)

    plan = plan_init(root, today=TODAY)

    assert plan.is_empty
    assert [line for line in plan.diff().splitlines() if line.startswith("+ ")] == []
    assert plan.diff() == apply_init(plan_init(root, today=TODAY)).diff()


def test_a_refused_plan_renders_the_refusal(tmp_path):
    root = tmp_path / "works"
    (root / "okf").mkdir(parents=True)
    (root / "okf" / "index.md").write_text("# My authored index\n", encoding="utf-8")

    plan = plan_init(root, today=TODAY, topic="Demo")

    assert not plan.ok
    assert [line for line in plan.diff().splitlines() if line.startswith("! index.md: ")]
