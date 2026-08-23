"""The refresher's four tools and the loop around them. No live model call."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_core.scan.prose_refresh import (
    build_prose_refresh_tools,
    is_denied_name,
    run_prose_refresh,
)
from graph_works_core.scan.scan_contract import ProseRefreshTask
from ingest_helpers import FakeLLM, FakeResponse
from langchain_core.tools import tool


@pytest.fixture
def roots(tmp_path):
    entity = tmp_path / "repo" / "packages" / "widgets"
    (entity / "src").mkdir(parents=True)
    (entity / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (entity / "pyproject.toml").write_text("[project]\nname = 'widgets'\n", encoding="utf-8")
    (tmp_path / "repo" / "secret.txt").write_text("do not read me\n", encoding="utf-8")
    bundle = tmp_path / "okf"
    (bundle / "packages").mkdir(parents=True)
    (bundle / "packages" / "widgets.md").write_text("---\ntype: Package\n---\n\n## Purpose\n", encoding="utf-8")
    (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
    return entity, bundle


def _task(entity_root) -> ProseRefreshTask:
    return ProseRefreshTask(
        uri="pkg:acme/demo/widgets",
        kind="Package",
        name="widgets",
        page_path="packages/widgets.md",
        entity_root=str(entity_root),
        trigger="first_fill",
        prose_sections={"## Purpose": ""},
    )


def _by_name(tools):
    return {agent_tool.name: agent_tool for agent_tool in tools}


def test_the_three_file_tools_read_inside_their_roots(roots):
    entity, bundle = roots
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert "print('a')" in tools["read_repo_file"].invoke({"path": "src/a.py"})
    assert "a.py" in tools["list_repo_tree"].invoke({"path": "src"})
    assert "## Purpose" in tools["read_bundle_page"].invoke({"path": "packages/widgets.md"})


@pytest.mark.parametrize("escape", ["../secret.txt", "/etc/hosts", "src/../../secret.txt"])
def test_read_repo_file_refuses_anything_outside_the_entity_root(roots, escape):
    entity, bundle = roots
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_repo_file"].invoke({"path": escape}).startswith("ERROR:")


def test_a_path_resolve_can_not_even_parse_is_an_error_string_not_an_exception(roots):
    """A model-supplied path with an embedded null byte makes `Path.resolve()`
    raise `ValueError` -- `resolve_under` must swallow that itself so a tool
    body needs no `try/except` of its own around the containment check."""
    entity, bundle = roots
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_repo_file"].invoke({"path": "src/a.py\x00../../secret.txt"}).startswith("ERROR:")


def test_read_bundle_page_refuses_anything_outside_the_bundle(roots):
    entity, bundle = roots
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_bundle_page"].invoke({"path": "../outside.md"}).startswith("ERROR:")


def test_a_symlink_out_of_the_root_is_refused(roots, tmp_path):
    entity, bundle = roots
    (entity / "escape.txt").symlink_to(tmp_path / "repo" / "secret.txt")
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_repo_file"].invoke({"path": "escape.txt"}).startswith("ERROR:")


def test_a_missing_file_is_an_error_string_not_an_exception(roots):
    entity, bundle = roots
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_repo_file"].invoke({"path": "src/nope.py"}).startswith("ERROR:")


def test_only_the_two_allowed_graph_tools_survive(roots):
    entity, bundle = roots

    @tool
    def cg_find(query: str) -> str:
        """find"""
        return query

    @tool
    def cg_callers(name: str) -> str:
        """callers"""
        return name

    names = set(
        _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle, graph_tools=[cg_find, cg_callers]))
    )
    assert "cg_find" in names
    assert "cg_callers" not in names


def test_the_repo_tools_refuse_when_the_task_has_no_entity_root(roots):
    _entity, bundle = roots
    task = ProseRefreshTask(
        uri="pkg:acme/demo/widgets",
        kind="Package",
        name="widgets",
        page_path="packages/widgets.md",
        entity_root="",
        trigger="first_fill",
        prose_sections={"## Purpose": ""},
    )
    tools = _by_name(build_prose_refresh_tools(task, bundle_root=bundle))
    assert tools["read_repo_file"].invoke({"path": "src/a.py"}) == "ERROR: this entity has no repository root"
    assert tools["list_repo_tree"].invoke({"path": ""}) == "ERROR: this entity has no repository root"


def test_list_repo_tree_refuses_a_path_that_is_not_a_directory(roots):
    entity, bundle = roots
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["list_repo_tree"].invoke({"path": "src/a.py"}).startswith("ERROR: not a directory")


def test_read_bundle_page_refuses_a_path_that_is_not_a_file(roots):
    entity, bundle = roots
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_bundle_page"].invoke({"path": "packages"}).startswith("ERROR: not a readable page")


def test_list_repo_tree_truncates_past_the_entry_cap(roots):
    entity, bundle = roots
    big = entity / "many"
    big.mkdir()
    for i in range(210):
        (big / f"f{i}.txt").write_text("x", encoding="utf-8")
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    listing = tools["list_repo_tree"].invoke({"path": "many"})
    assert "[TRUNCATED after 200 of 210 entries]" in listing


def test_read_repo_file_reports_an_os_error_as_an_error_string(roots, monkeypatch):
    entity, bundle = roots

    def boom(self, *a, **k):
        raise OSError("boom")

    monkeypatch.setattr(Path, "read_text", boom)
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_repo_file"].invoke({"path": "src/a.py"}) == "ERROR: boom"


def test_read_bundle_page_reports_an_os_error_as_an_error_string(roots, monkeypatch):
    entity, bundle = roots

    def boom(self, *a, **k):
        raise OSError("boom")

    monkeypatch.setattr(Path, "read_text", boom)
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["read_bundle_page"].invoke({"path": "packages/widgets.md"}) == "ERROR: boom"


def test_list_repo_tree_reports_an_os_error_as_an_error_string(roots, monkeypatch):
    entity, bundle = roots

    def boom(self, *a, **k):
        raise OSError("boom")

    monkeypatch.setattr(Path, "iterdir", boom)
    tools = _by_name(build_prose_refresh_tools(_task(entity), bundle_root=bundle))
    assert tools["list_repo_tree"].invoke({"path": "src"}) == "ERROR: boom"


async def test_a_successful_loop_returns_raw_unsanitized_sections(roots):
    entity, bundle = roots
    payload = {"sections": {"## Purpose": "Real prose.", "## Files": "generated!"}}
    llm = FakeLLM(FakeResponse(json.dumps(payload)))
    result = await run_prose_refresh(_task(entity), llm=llm, bundle_root=bundle)
    assert result.error is None
    # `## Files` is undeclared -- phase 3 drops it, not this function.
    assert result.sections == payload["sections"]


async def test_an_unparseable_answer_becomes_an_error(roots):
    entity, bundle = roots
    result = await run_prose_refresh(_task(entity), llm=FakeLLM(FakeResponse("not json")), bundle_root=bundle)
    assert result.sections == {}
    assert result.error is not None


async def test_an_empty_model_answer_becomes_an_error(roots):
    entity, bundle = roots
    result = await run_prose_refresh(_task(entity), llm=FakeLLM(FakeResponse("")), bundle_root=bundle)
    assert result.error is not None


async def test_hitting_the_iteration_cap_with_usable_text_is_not_an_error(roots):
    """`agent_loop` reports a cap-after-text loop as `status="ok"` with an error
    *note*; `run_prose_refresh` must not let that note surface as `.error`."""
    entity, bundle = roots
    payload = {"sections": {"## Purpose": "Real prose."}}
    call = {"name": "read_bundle_page", "args": {"path": "packages/widgets.md"}, "id": "1"}
    # `MAX_REFRESH_ITERS` responses, every one still calling a tool, so the loop
    # never finds a tool-call-free turn to exit on and runs out the cap instead.
    llm = FakeLLM(*(FakeResponse(json.dumps(payload), tool_calls=[call]) for _ in range(6)))
    result = await run_prose_refresh(_task(entity), llm=llm, bundle_root=bundle)
    assert result.error is None
    assert result.sections == payload["sections"]


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.production",
        "server.pem",
        "tls.key",
        "cert.p12",
        "id_rsa",
        "id_rsa.pub",
        "id_ed25519",
        ".netrc",
        ".npmrc",
        ".ENV",
        "SERVER.PEM",
    ],
)
def test_credential_shaped_names_are_denied(name):
    assert is_denied_name(name) is True


@pytest.mark.parametrize("name", ["main.py", "README.md", "environment.yml", "keyring.py", "pyproject.toml"])
def test_ordinary_names_are_not_denied(name):
    assert is_denied_name(name) is False


def test_a_dotenv_under_the_entity_root_is_unreadable(tmp_path):
    root = tmp_path / "entity"
    root.mkdir()
    (root / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    task = ProseRefreshTask(
        uri="pkg:a/b/c",
        kind="Package",
        name="c",
        page_path="packages/c.md",
        entity_root=str(root),
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path / "bundle")}

    refused = tools["read_repo_file"].invoke({"path": ".env"})
    assert refused == "ERROR: refused: credential-shaped path: .env"
    assert "hunter2" not in refused
    assert tools["read_repo_file"].invoke({"path": "main.py"}) == "x = 1\n"


def test_a_denied_name_is_refused_whether_or_not_it_exists(tmp_path):
    """The refusal precedes the `is_file()` check, so it does not answer
    "does this repo have a .env?" as a side effect."""
    root = tmp_path / "entity"
    root.mkdir()
    task = ProseRefreshTask(
        uri="pkg:a/b/c",
        kind="Package",
        name="c",
        page_path="packages/c.md",
        entity_root=str(root),
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path / "bundle")}
    assert tools["read_repo_file"].invoke({"path": ".env"}) == "ERROR: refused: credential-shaped path: .env"


def test_a_dotenv_is_omitted_from_the_listing(tmp_path):
    root = tmp_path / "entity"
    root.mkdir()
    (root / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    (root / "id_ed25519").write_text("-----BEGIN\n", encoding="utf-8")
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    task = ProseRefreshTask(
        uri="pkg:a/b/c",
        kind="Package",
        name="c",
        page_path="packages/c.md",
        entity_root=str(root),
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path / "bundle")}

    listing = tools["list_repo_tree"].invoke({"path": ""})
    assert "main.py" in listing
    assert ".env" not in listing
    assert "id_ed25519" not in listing


def test_a_directory_of_only_denied_entries_lists_as_empty(tmp_path):
    root = tmp_path / "entity"
    root.mkdir()
    (root / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    task = ProseRefreshTask(
        uri="pkg:a/b/c",
        kind="Package",
        name="c",
        page_path="packages/c.md",
        entity_root=str(root),
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path / "bundle")}
    assert tools["list_repo_tree"].invoke({"path": ""}) == "(empty)"


def test_a_denied_directory_cannot_be_listed(tmp_path):
    """A directory named .env must be refused, not listed."""
    root = tmp_path / "entity"
    root.mkdir()
    (root / ".env").mkdir()
    (root / ".env" / "local").write_text("SECRET=hunter2\n", encoding="utf-8")
    task = ProseRefreshTask(
        uri="pkg:a/b/c",
        kind="Package",
        name="c",
        page_path="packages/c.md",
        entity_root=str(root),
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path / "bundle")}
    result = tools["list_repo_tree"].invoke({"path": ".env"})
    assert result == "ERROR: refused: credential-shaped path: .env"


def test_a_file_inside_a_denied_directory_cannot_be_read(tmp_path):
    """A file inside a denied directory must be refused, blocking lateral traversal."""
    root = tmp_path / "entity"
    root.mkdir()
    (root / ".env").mkdir()
    (root / ".env" / "local").write_text("SECRET=hunter2\n", encoding="utf-8")
    task = ProseRefreshTask(
        uri="pkg:a/b/c",
        kind="Package",
        name="c",
        page_path="packages/c.md",
        entity_root=str(root),
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path / "bundle")}
    result = tools["read_repo_file"].invoke({"path": ".env/local"})
    assert result == "ERROR: refused: credential-shaped path: .env/local"


def test_listing_the_entity_root_itself_still_works(tmp_path):
    """The root itself must be listable even when checking all components."""
    root = tmp_path / "entity"
    root.mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    task = ProseRefreshTask(
        uri="pkg:a/b/c",
        kind="Package",
        name="c",
        page_path="packages/c.md",
        entity_root=str(root),
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path / "bundle")}
    listing = tools["list_repo_tree"].invoke({"path": ""})
    assert "main.py" in listing


def test_the_file_tools_refuse_an_entity_with_no_repository_root(tmp_path):
    """The `repo_path is None` path -- a Dependency page. Its `entity_root` is
    the empty string, so both file tools have nothing to resolve against."""
    task = ProseRefreshTask(
        uri="dependency:pypi/httpx",
        kind="Dependency",
        name="httpx",
        page_path="dependencies/pypi/httpx.md",
        entity_root="",
        trigger="first_fill",
    )
    tools = {t.name: t for t in build_prose_refresh_tools(task, bundle_root=tmp_path)}
    assert tools["read_repo_file"].invoke({"path": "setup.py"}) == "ERROR: this entity has no repository root"
    assert tools["list_repo_tree"].invoke({"path": ""}) == "ERROR: this entity has no repository root"
