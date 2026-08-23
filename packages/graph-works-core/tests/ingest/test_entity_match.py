"""The EntityMatcher: two lookups, one naming rule, and the two absences."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from code_graph_io import open_reader
from code_graph_io.update import run_workspace
from doc_wiki_okf.ingest.seams import NO_ENTITY
from graph_works_core.ingest.entity_match import (
    ENTITY_KINDS,
    entity_matcher,
    lookup_by_name,
    lookup_by_path,
    page_id_for,
)
from ingest_helpers import FakeReader
from okf_ext.bundle import SCHEMA_DIRNAME


def _schema_set(tmp_path: Path):
    from okf_ext.schemas import load_schemas

    directory = tmp_path / SCHEMA_DIRNAME
    directory.mkdir()
    (directory / "Package.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",'
        ' "required": ["type"], "properties": {"type": {"const": "Package"}},'
        ' "x-okf-directory": "packages/"}',
        encoding="utf-8",
    )
    return load_schemas(directory)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _real_repo(root: Path, *, name: str, package: str) -> tuple[Path, Path]:
    repo = root / name
    source = repo / "src" / "shared" / "thing.py"
    source.parent.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "{package}"\nversion = "0.1.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", f"https://github.com/acme/{name}.git")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo, source


def test_a_file_inside_a_graphed_package_matches_by_path(tmp_path):
    repo = tmp_path / "repo"
    (repo / "packages/okf-io/src").mkdir(parents=True)
    source = repo / "packages/okf-io/src/thing.py"
    source.write_text("x = 1", encoding="utf-8")
    reader = FakeReader(by_file_uri={"file:acme/demo/packages/okf-io/src/thing.py": ("okf-io", "pkg:acme/demo/okf-io")})

    match = entity_matcher(
        reader,
        _schema_set(tmp_path),
        repository_resources={repo.resolve(): "repo:acme/demo"},
    )(repo, source, "Anything")

    assert match.uri == "pkg:acme/demo/okf-io"
    assert match.entity_filename == "repositories/demo/packages/okf-io"


def test_a_source_outside_the_repo_falls_through_to_the_name_lookup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "elsewhere.md"
    outside.write_text("hi", encoding="utf-8")
    reader = FakeReader(by_name={"Okf Io": [("okf-io", "pkg:acme/demo/okf-io", "package")]})

    match = entity_matcher(
        reader,
        _schema_set(tmp_path),
        repository_resources={repo.resolve(): "repo:acme/demo"},
    )(repo, outside, "Okf Io")

    assert match.uri == "pkg:acme/demo/okf-io"
    assert match.entity_filename == "repositories/demo/packages/okf-io"


def test_no_hit_anywhere_is_NO_ENTITY(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "loose.md"
    source.write_text("hi", encoding="utf-8")

    assert (
        entity_matcher(
            FakeReader(),
            _schema_set(tmp_path),
            repository_resources={repo.resolve(): "repo:acme/demo"},
        )(repo, source, "Loose")
        == NO_ENTITY
    )


def test_an_ambiguous_name_is_a_miss_and_is_logged_once(tmp_path, caplog):
    reader = FakeReader(by_name={"run": [("run", "fn:a#run", "function"), ("run", "fn:b#run", "function")]})
    with caplog.at_level(logging.WARNING):
        assert lookup_by_name(reader, "run") is None
    assert sum("matches 2 graph nodes" in record.message for record in caplog.records) == 1


def test_a_blank_name_never_reaches_the_reader():
    reader = FakeReader(by_name={"": [("", "pkg:x", "package")]})
    assert lookup_by_name(reader, "") is None


def test_a_symbol_hit_carries_a_uri_but_no_page(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "loose.md"
    source.write_text("hi", encoding="utf-8")
    reader = FakeReader(by_name={"Splice": [("Splice", "cls:okf_ext.splice#Splice", "class")]})

    match = entity_matcher(
        reader,
        _schema_set(tmp_path),
        repository_resources={repo.resolve(): "repo:acme/demo"},
    )(repo, source, "Splice")

    assert match.uri == "cls:okf_ext.splice#Splice"
    assert match.entity_filename is None


def test_the_page_id_uses_resource_identity_and_the_placement_policy(tmp_path):
    schema_set = _schema_set(tmp_path)

    assert (
        page_id_for(schema_set, resource="pkg:acme/demo/@babel/core", kind="package")
        == "repositories/demo/packages/@babel__core"
    )
    assert page_id_for(schema_set, resource="pkg:acme/other/@babel/core", kind="package") == (
        "repositories/other/packages/@babel__core"
    )


def test_a_schema_set_without_Package_declines_rather_than_raising(tmp_path, caplog):
    from okf_ext.schemas import load_schemas

    empty = tmp_path / "none"
    empty.mkdir()
    # `load_schemas` raises `SchemaError` for a directory with *no* schema
    # files at all (that is overwhelmingly a wrong path, per its own
    # docstring) -- a different concern from what this test is about, which
    # is a schema *set* that simply never declared `Package`. A one-type set
    # that isn't `Package` exercises the same absence without tripping that
    # unrelated guard.
    (empty / "Other.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",'
        ' "required": ["type"], "properties": {"type": {"const": "Other"}},'
        ' "x-okf-directory": "others/"}',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        assert page_id_for(load_schemas(empty), resource="pkg:acme/demo/okf-io", kind="package") is None
    assert any("no Package schema" in record.message for record in caplog.records)


def test_lookup_by_path_returns_none_when_the_package_has_no_row(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("", encoding="utf-8")
    assert lookup_by_path(FakeReader(), repo, source, repository_resource="repo:acme/demo") is None


def test_the_name_lookup_is_restricted_to_the_four_entity_kinds():
    assert ENTITY_KINDS == ("class", "function", "method", "package")
    reader = FakeReader(by_name={"thing": [("thing", "file:thing", "file")]})
    assert lookup_by_name(reader, "thing") is None


def test_entity_match_scopes_same_relative_path_to_its_repository(tmp_path: Path) -> None:
    alpha, _alpha_source = _real_repo(tmp_path, name="alpha", package="alpha-pkg")
    beta, beta_source = _real_repo(tmp_path, name="beta", package="beta-pkg")
    graph_dir = tmp_path / "graph"
    run_workspace([alpha, beta], graph_dir=graph_dir, full=True)

    with open_reader(graph_dir=graph_dir) as reader:
        match = entity_matcher(
            reader,
            _schema_set(tmp_path),
            repository_resources={beta.resolve(): "repo:acme/beta"},
        )(beta, beta_source, "Anything")

    assert match.uri == "pkg:acme/beta/beta-pkg"
    assert match.entity_filename == "repositories/beta/packages/beta-pkg"
