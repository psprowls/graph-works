"""MSBuild solution/project scanning: .sln + .csproj -> kind:solution/package nodes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from code_graph_io import csharp_projects, store
from code_graph_io._ignore import compile_ignore
from code_graph_io.uri import RepoContext

_CTX = RepoContext(org="test", repo="repo")

_CSPROJ_SIMPLE = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
  </ItemGroup>
</Project>
"""


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "code.db"
    c = store.connect(db, create=True)
    yield c
    c.close()


def test_standalone_csproj_no_solution(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "MyLib"
    proj_dir.mkdir(parents=True)
    (proj_dir / "MyLib.csproj").write_text(_CSPROJ_SIMPLE)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    row = conn.execute("SELECT name FROM nodes WHERE kind='package' AND name='MyLib'").fetchone()
    assert row is not None
    solutions = conn.execute("SELECT name FROM nodes WHERE kind='solution'").fetchall()
    assert solutions == []


def test_solution_with_two_projects(tmp_path: Path, conn: sqlite3.Connection) -> None:
    (tmp_path / "src" / "Api").mkdir(parents=True)
    (tmp_path / "src" / "Core").mkdir(parents=True)
    (tmp_path / "src" / "Api" / "Api.csproj").write_text(_CSPROJ_SIMPLE)
    (tmp_path / "src" / "Core" / "Core.csproj").write_text(_CSPROJ_SIMPLE)
    sln = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        "# Visual Studio Version 17\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Api", '
        '"src\\Api\\Api.csproj", "{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}"\n'
        "EndProject\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Core", '
        '"src\\Core\\Core.csproj", "{BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB}"\n'
        "EndProject\n"
    )
    (tmp_path / "MyApp.sln").write_text(sln)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    sol = conn.execute("SELECT id, name FROM nodes WHERE kind='solution'").fetchone()
    assert sol is not None
    assert sol[1] == "MyApp"
    edges = conn.execute("SELECT dst FROM edges WHERE src=? AND kind='groups_project'", (sol[0],)).fetchall()
    assert len(edges) == 2


def test_package_reference_becomes_nuget_dependency(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "MyLib"
    proj_dir.mkdir(parents=True)
    (proj_dir / "MyLib.csproj").write_text(_CSPROJ_SIMPLE)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    row = conn.execute(
        "SELECT name, attrs_json FROM nodes WHERE kind='dependency' AND name='Newtonsoft.Json'"
    ).fetchone()
    assert row is not None
    attrs = json.loads(row[1])
    assert attrs["ecosystem"] == "nuget"
    used_by = conn.execute(
        "SELECT e.kind FROM edges e JOIN nodes n ON e.src = n.id "
        "WHERE n.kind='package' AND n.name='MyLib' AND e.kind='used_by'"
    ).fetchall()
    assert len(used_by) == 1


def test_package_reference_nested_version_is_recorded(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "MyLib"
    proj_dir.mkdir(parents=True)
    (proj_dir / "MyLib.csproj").write_text(
        '<Project><ItemGroup><PackageReference Include="Newtonsoft.Json">'
        "<Version>13.0.3</Version></PackageReference></ItemGroup></Project>"
    )

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='dependency' AND name='Newtonsoft.Json'").fetchone()
    assert row is not None
    assert json.loads(row[0])["versions_in_use"] == ["13.0.3"]


def test_versions_in_use_accumulates_across_projects(tmp_path: Path, conn: sqlite3.Connection) -> None:
    lib_dir = tmp_path / "src" / "LibA"
    other_dir = tmp_path / "src" / "LibB"
    lib_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    csproj = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="{version}" />
  </ItemGroup>
</Project>
"""
    (lib_dir / "LibA.csproj").write_text(csproj.format(version="13.0.3"))
    (other_dir / "LibB.csproj").write_text(csproj.format(version="12.0.1"))

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='dependency' AND name='Newtonsoft.Json'").fetchone()
    assert row is not None
    attrs = json.loads(row[0])
    assert attrs["versions_in_use"] == ["12.0.1", "13.0.3"]


def test_csproj_under_skip_dir_is_ignored(tmp_path: Path, conn: sqlite3.Connection) -> None:
    skipped_dir = tmp_path / "node_modules" / "SomeLib"
    skipped_dir.mkdir(parents=True)
    (skipped_dir / "SomeLib.csproj").write_text(_CSPROJ_SIMPLE)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    row = conn.execute("SELECT name FROM nodes WHERE kind='package' AND name='SomeLib'").fetchone()
    assert row is None


def test_csproj_under_configured_ignore_is_excluded(tmp_path: Path, conn: sqlite3.Connection) -> None:
    fixtures_dir = tmp_path / "fixtures" / "Vendored"
    fixtures_dir.mkdir(parents=True)
    (fixtures_dir / "Vendored.csproj").write_text(_CSPROJ_SIMPLE)
    ignore = compile_ignore(["**/fixtures/**"])

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX, ignore=ignore)

    row = conn.execute("SELECT name FROM nodes WHERE kind='package' AND name='Vendored'").fetchone()
    assert row is None


def test_solution_does_not_create_package_for_ignored_project(tmp_path: Path, conn: sqlite3.Connection) -> None:
    project_dir = tmp_path / "fixtures" / "Vendored"
    project_dir.mkdir(parents=True)
    (project_dir / "Vendored.csproj").write_text(_CSPROJ_SIMPLE)
    (tmp_path / "App.sln").write_text(
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Vendored", '
        '"fixtures\\Vendored\\Vendored.csproj", "{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}"\nEndProject\n'
    )

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX, ignore=compile_ignore(["**/fixtures/**"]))

    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='Vendored'").fetchone() is None
    assert conn.execute("SELECT 1 FROM edges WHERE kind='groups_project'").fetchone() is None


def test_solution_does_not_create_package_for_malformed_project(tmp_path: Path, conn: sqlite3.Connection) -> None:
    project_dir = tmp_path / "src" / "Broken"
    project_dir.mkdir(parents=True)
    (project_dir / "Broken.csproj").write_text("<Project><Unclosed>")
    (tmp_path / "App.sln").write_text(
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Broken", '
        '"src\\Broken\\Broken.csproj", "{BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB}"\nEndProject\n'
    )

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='Broken'").fetchone() is None
    assert conn.execute("SELECT 1 FROM edges WHERE kind='groups_project'").fetchone() is None


def test_project_referenced_by_two_solutions(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "Shared"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Shared.csproj").write_text(_CSPROJ_SIMPLE)
    proj_line = (
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Shared", '
        '"src\\Shared\\Shared.csproj", "{CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC}"\nEndProject\n'
    )
    header = "Microsoft Visual Studio Solution File, Format Version 12.00\n# Visual Studio Version 17\n"
    (tmp_path / "Full.sln").write_text(header + proj_line)
    (tmp_path / "TestsOnly.sln").write_text(header + proj_line)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    solutions = conn.execute("SELECT id, name FROM nodes WHERE kind='solution'").fetchall()
    assert {name for _, name in solutions} == {"Full", "TestsOnly"}
    total_edges = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='groups_project'").fetchone()[0]
    assert total_edges == 2


def test_solution_pruned_on_rescan_after_deletion(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "MyLib"
    proj_dir.mkdir(parents=True)
    (proj_dir / "MyLib.csproj").write_text(_CSPROJ_SIMPLE)
    sln_path = tmp_path / "MyApp.sln"
    sln_path.write_text(
        "Microsoft Visual Studio Solution File, Format Version 12.00\n# Visual Studio Version 17\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "MyLib", "src\\MyLib\\MyLib.csproj", '
        '"{DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD}"\nEndProject\n'
    )

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='solution'").fetchone()[0] == 1

    sln_path.unlink()
    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='solution'").fetchone()[0] == 0


def test_csproj_pruned_on_rescan_after_deletion(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "MyLib"
    proj_dir.mkdir(parents=True)
    csproj_path = proj_dir / "MyLib.csproj"
    csproj_path.write_text(_CSPROJ_SIMPLE)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package' AND name='MyLib'").fetchone()[0] == 1

    csproj_path.unlink()
    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package' AND name='MyLib'").fetchone()[0] == 0


def test_sln_project_outside_repo_root_is_skipped(tmp_path: Path, conn: sqlite3.Connection) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    shared_dir = tmp_path / "Shared"
    shared_dir.mkdir()
    (shared_dir / "Shared.csproj").write_text(_CSPROJ_SIMPLE)
    sln = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        "# Visual Studio Version 17\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Shared", '
        '"..\\Shared\\Shared.csproj", "{FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF}"\n'
        "EndProject\n"
    )
    (repo_root / "MyApp.sln").write_text(sln)

    # Must not raise.
    csharp_projects.refresh(conn, repo_root=repo_root, ctx=_CTX)

    edges = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='groups_project'").fetchone()[0]
    assert edges == 0


def test_external_projects_recorded_on_solution_node(tmp_path: Path, conn: sqlite3.Connection) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    shared_dir = tmp_path / "Shared"
    shared_dir.mkdir()
    (shared_dir / "Shared.csproj").write_text(_CSPROJ_SIMPLE)
    sln = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        "# Visual Studio Version 17\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Shared", '
        '"..\\Shared\\Shared.csproj", "{FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF}"\n'
        "EndProject\n"
    )
    (repo_root / "MyApp.sln").write_text(sln)

    csharp_projects.refresh(conn, repo_root=repo_root, ctx=_CTX)

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='solution' AND name='MyApp'").fetchone()
    assert row is not None
    attrs = json.loads(row[0])
    assert attrs["external_projects"] == ["../Shared/Shared.csproj"]


def test_solution_node_omits_external_projects_when_all_in_tree(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "MyLib"
    proj_dir.mkdir(parents=True)
    (proj_dir / "MyLib.csproj").write_text(_CSPROJ_SIMPLE)
    sln = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        "# Visual Studio Version 17\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "MyLib", '
        '"src\\MyLib\\MyLib.csproj", "{DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD}"\n'
        "EndProject\n"
    )
    (tmp_path / "MyApp.sln").write_text(sln)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='solution' AND name='MyApp'").fetchone()
    assert row is not None
    attrs = json.loads(row[0]) if row[0] is not None else {}
    assert "external_projects" not in attrs


def test_sln_display_name_differs_from_csproj_stem(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "Core"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Core.csproj").write_text(_CSPROJ_SIMPLE)
    sln = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        "# Visual Studio Version 17\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "MyApp.Core", '
        '"src\\Core\\Core.csproj", "{11111111-1111-1111-1111-111111111111}"\n'
        "EndProject\n"
    )
    (tmp_path / "MyApp.sln").write_text(sln)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    pkg_rows = conn.execute("SELECT id, name, uri, attrs_json FROM nodes WHERE kind='package'").fetchall()
    assert len(pkg_rows) == 1
    pkg_id, name, uri, attrs_json = pkg_rows[0]
    assert name == "Core"
    assert uri is not None
    assert attrs_json is not None

    sol_id = conn.execute("SELECT id FROM nodes WHERE kind='solution'").fetchone()[0]
    edge_dst = conn.execute("SELECT dst FROM edges WHERE src=? AND kind='groups_project'", (sol_id,)).fetchone()[0]
    assert edge_dst == pkg_id


def test_sln_entry_referencing_missing_csproj_is_skipped(tmp_path: Path, conn: sqlite3.Connection) -> None:
    sln = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        "# Visual Studio Version 17\n"
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Ghost", '
        '"src\\Ghost\\Ghost.csproj", "{22222222-2222-2222-2222-222222222222}"\n'
        "EndProject\n"
    )
    (tmp_path / "MyApp.sln").write_text(sln)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE kind='groups_project'").fetchone()[0] == 0


def test_sln_non_csproj_entry_is_ignored(tmp_path: Path, conn: sqlite3.Connection) -> None:
    # A Solution Folder entry: name has no .csproj target.
    sln = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        "# Visual Studio Version 17\n"
        'Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "Solution Items", '
        '"Solution Items", "{33333333-3333-3333-3333-333333333333}"\n'
        "EndProject\n"
    )
    (tmp_path / "MyApp.sln").write_text(sln)

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    sol_row = conn.execute("SELECT id FROM nodes WHERE kind='solution'").fetchone()
    assert sol_row is not None
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE kind='groups_project'").fetchone()[0] == 0


def test_malformed_csproj_is_skipped(tmp_path: Path, conn: sqlite3.Connection) -> None:
    proj_dir = tmp_path / "src" / "Broken"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Broken.csproj").write_text("<Project><Unclosed>\n")

    csharp_projects.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package'").fetchone()[0] == 0
