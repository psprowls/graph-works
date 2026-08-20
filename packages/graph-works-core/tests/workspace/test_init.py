"""The six acts of workspace init, and the vocabulary they report in."""

from __future__ import annotations

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

    sections = load_sections(result.layout.config_dir / "_sections")
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


# --- act 1: directories -----------------------------------------------------


def test_apply_creates_every_layout_directory(tmp_path):
    result = _init(tmp_path / "works")
    for directory in result.layout.directories:
        assert directory.is_dir(), directory


# --- act 2: the workspace's own gitignore -----------------------------------


def test_the_gitignore_holds_the_two_gitignored_members(tmp_path):
    result = _init(tmp_path / "works")
    text = (result.layout.root / ".gitignore").read_text(encoding="utf-8")
    assert "/_cache/" in text
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
    (root / ".gitignore").write_text("/_cache/\n*.tmp", encoding="utf-8")
    _init(root)
    text = (root / ".gitignore").read_text(encoding="utf-8")
    assert text.count("/_cache/") == 1
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
    assert not (root / ".gitignore").exists()


# --- act 3: the manifest ----------------------------------------------------


def test_the_manifest_is_written_once_and_never_overwritten(tmp_path):
    root = tmp_path / "works"
    _init(root, topic="My Works")
    before = (root / "workspace.yaml").read_text(encoding="utf-8")
    assert "My Works" in before
    _init(root, topic="A Different Topic")
    assert (root / "workspace.yaml").read_text(encoding="utf-8") == before


# --- act 4: the bundle scaffold, and its declaration routing ----------------


def test_the_scaffold_routes_declarations_and_leaves_the_rest_in_the_bundle(tmp_path):
    result = _init(tmp_path / "works")
    layout = result.layout
    assert (layout.bundle_dir / "index.md").is_file()
    assert (layout.bundle_dir / "log.md").is_file()
    assert (layout.config_dir / "_tags.yaml").is_file()
    assert not (layout.bundle_dir / "_tags.yaml").exists()


# --- act 5: the installers --------------------------------------------------


def test_both_installers_land_their_declarations_under_the_config_dir(tmp_path):
    result = _init(tmp_path / "works")
    layout = result.layout
    assert (layout.config_dir / "_schema" / "Package.schema.json").is_file()
    assert (layout.config_dir / "_sections" / "Package.yaml").is_file()
    assert (layout.config_dir / "_schema" / "Feature.schema.json").is_file()
    assert (layout.config_dir / "_sections" / "Feature.yaml").is_file()
    assert result.ok


def test_the_installer_roster_is_injectable(tmp_path):
    result = _init(tmp_path / "works", installers=())
    assert result.installs == ()
    assert (result.layout.bundle_dir / "index.md").is_file()
    assert not (result.layout.config_dir / "_schema").exists()


# --- act 6: _repositories.yaml, seeded from the layout ----------------------


def test_repositories_yaml_is_written_from_the_layout(tmp_path):
    from code_wiki_okf.config import load_config

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    result = _init(repo / ".works")
    config = load_config(result.layout.bundle_dir)
    assert config.graph_dir == result.layout.cache_dir
    assert config.declarations_dir == result.layout.config_dir
    assert [entry.name for entry in config.repos] == [repo.name]
    assert config.repos[0].path == repo.resolve()
    assert config.repos[0].ignore == (".works/**",)


def test_repositories_yaml_outside_a_repo_declares_no_repository(tmp_path):
    from code_wiki_okf.config import load_config

    result = _init(tmp_path / "works")
    config = load_config(result.layout.bundle_dir)
    assert config.repos == ()
    assert config.graph_dir == result.layout.cache_dir


def test_an_explicit_repo_root_overrides_detection(tmp_path):
    from code_wiki_okf.config import load_config

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _init(tmp_path / "works", repo_root=elsewhere)
    assert result.layout.repo_root == elsewhere.resolve()
    assert [entry.name for entry in load_config(result.layout.bundle_dir).repos] == ["elsewhere"]


def test_a_hand_edited_repositories_yaml_is_left_alone(tmp_path):
    root = tmp_path / "works"
    _init(root)
    path = root / "okf" / "_repositories.yaml"
    edited = path.read_text(encoding="utf-8") + "\nstate_gate:\n  enabled: false\n"
    path.write_text(edited, encoding="utf-8")
    _init(root)
    assert path.read_text(encoding="utf-8") == edited


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
    assert "+ .gitignore" in lines
    assert "+ workspace.yaml" in lines
    assert "+ okf/_repositories.yaml" in lines
    assert "+ index.md" in lines
    assert "+ _schema/Package.schema.json" in lines


def test_the_first_plan_over_reports_the_installer_seeded_repositories_file(tmp_path):
    """The asymmetry `WorkspacePlan`'s docstring records, asserted as behavior.

    Installer previews are computed against the filesystem as it stands, so on a
    fresh root `code_wiki_okf` still previews `_repositories.yaml` — a file act 4
    will have written by the time its installer actually runs. The renderer must
    report what the plan says, not what the apply will do.
    """
    lines = plan_init(tmp_path / "works", today=TODAY).diff().splitlines()

    assert "+ okf/_repositories.yaml" in lines
    assert "+ _repositories.yaml" in lines


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
