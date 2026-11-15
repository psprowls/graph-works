"""Acceptance suite for the shared AST boundary-test mechanism."""

from __future__ import annotations

import ast

import pytest
from gw_kernel.testing.boundaries import (
    Finding,
    discovery_lookups,
    manifest_reads,
    module_name,
    scan_imports,
    under,
)


def reads(source: str) -> list[str]:
    scan = scan_imports(source, module="example.commands")
    return [finding.text for finding in manifest_reads(scan.tree, scan.aliases)]


@pytest.mark.parametrize(
    "expression,forbidden",
    [
        ('lambda manifest: manifest.read_text(encoding="utf-8")', False),
        ("lambda manifest=manifest.read_text(): manifest", True),
        ("lambda notes: manifest.read_text()", True),
        ('lambda notes: Path("workspace.yaml").read_text()', True),
        ('lambda manifest=Path("notes.md"): manifest.read_text()', False),
        ('lambda manifest=Path("workspace.yaml"): manifest.read_text()', True),
    ],
)
def test_lambda_binding_scope(expression, forbidden):
    source = 'from pathlib import Path\nmanifest = Path("workspace.yaml")\nread_notes = ' + expression
    assert bool(reads(source)) is forbidden


COMPREHENSIONS = (
    "[{read} for manifest in {items}]",
    "{{{read} for manifest in {items}}}",
    "{{manifest: {read} for manifest in {items}}}",
    "({read} for manifest in {items})",
)


@pytest.mark.parametrize("template", COMPREHENSIONS)
@pytest.mark.parametrize("scenario", ["notes", "local-manifest", "outer-read", "first-iterable", "restored"])
def test_comprehension_binding_scope(template, scenario):
    outer = "notes.md" if scenario == "local-manifest" else "workspace.yaml"
    items = '[Path("workspace.yaml")]' if scenario == "local-manifest" else '[Path("notes.md")]'
    read = 'manifest.read_text(encoding="utf-8")'
    if scenario == "outer-read":
        template = template.replace("for manifest", "for note")
    elif scenario == "first-iterable":
        items = '[Path(manifest.read_text(encoding="utf-8"))]'
    expression = template.format(read=read, items=items)
    source = f'from pathlib import Path\nmanifest = Path("{outer}")\nnotes = {expression}\n'
    if scenario == "restored":
        source += 'manifest.read_text(encoding="utf-8")\n'
    assert bool(reads(source)) is (scenario != "notes")


@pytest.mark.parametrize("template", COMPREHENSIONS)
@pytest.mark.parametrize("forbidden", [False, True])
def test_async_comprehension_binding_scope(template, forbidden):
    read = 'Path("workspace.yaml").read_text()' if forbidden else "manifest.read_text()"
    expression = template.replace("for manifest", "async for manifest").format(read=read, items="paths")
    source = (
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        f"async def read_notes(paths):\n    return {expression}\n"
    )
    assert bool(reads(source)) is forbidden


@pytest.mark.parametrize(
    "body,forbidden",
    [
        ("read_notes = lambda: manifest.read_text()", False),
        ("read_notes = lambda notes=manifest.read_text(): notes", True),
        ('notes = [manifest.read_text() for note in [Path("notes.md")]]', False),
        ("notes = [note for note in manifest.read_text().splitlines()]", True),
    ],
)
def test_class_namespace_is_used_for_defaults_and_first_iterable_only(body, forbidden):
    source = (
        'from pathlib import Path\nmanifest = Path("notes.md")\n'
        f'class Manifest:\n    manifest = Path("workspace.yaml")\n    {body}\n'
    )
    assert bool(reads(source)) is forbidden


def test_comprehension_tuple_targets_and_later_iterables_use_local_bindings():
    source = (
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        'notes = [line for manifest, other in [(Path("notes.md"), 1)]\n'
        "         for line in manifest.read_text().splitlines() if other]\n"
    )
    assert reads(source) == []
    assert reads(source + "manifest.read_text()\n") == ["manifest.read_text()"]


UNPACKED = (
    # (target, iterable, read, forbidden)
    ("manifest, other", '[(Path("workspace.yaml"), Path("notes.md"))]', "other", False),
    ("manifest, other", '[(Path("workspace.yaml"), Path("notes.md"))]', "manifest", True),
    ("a, (b, c)", '[(Path("notes.md"), (Path("workspace.yaml"), Path("x.md")))]', "b", True),
    ("a, (b, c)", '[(Path("notes.md"), (Path("workspace.yaml"), Path("x.md")))]', "a", False),
    ("a, (b, c)", '[(Path("notes.md"), (Path("workspace.yaml"), Path("x.md")))]', "c", False),
    ("a, b", "rows", "a", True),
    ("a, b", "rows", "b", True),
    ("a, b", '[(Path("workspace.yaml"), Path("n.md"), 1)]', "b", True),
    ("a, *b", '[(Path("workspace.yaml"), Path("n.md"))]', "b", True),
    ("a, b", '[(Path("n.md"), Path("workspace.yaml")), x]', "a", True),
)


@pytest.mark.parametrize("template", COMPREHENSIONS)
@pytest.mark.parametrize("target,items,read,forbidden", UNPACKED)
def test_unpacked_comprehension_targets_are_attributed_per_position(template, target, items, read, forbidden):
    expression = template.replace("for manifest", f"for {target}").format(read=f"{read}.read_text()", items=items)
    source = f"from pathlib import Path\nrows = [Path('workspace.yaml')]\nnotes = {expression}\n"
    assert bool(reads(source)) is forbidden


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\ndef read(manifest=Path('workspace.yaml')):\n    return manifest.read_text()",
        "from pathlib import Path\ndef read(*, manifest=Path('workspace.yaml')):\n    return manifest.read_bytes()",
        "from pathlib import Path\nmanifest = Path('workspace.yaml')\n"
        "def read(path=manifest, /):\n    return path.open()",
        "from pathlib import Path\nasync def read(manifest=Path('workspace.yaml')):\n    return manifest.read_text()",
        "from pathlib import Path\nclass Reader:\n    manifest = Path('workspace.yaml')\n"
        "    def read(self, path=manifest):\n        return path.read_text()",
    ],
)
def test_manifest_function_defaults_are_rejected(source):
    assert reads(source)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\ndef read(manifest=Path('notes.md')):\n    return manifest.read_text()",
        "from pathlib import Path\ndef read(manifest=Path('workspace.yaml')):\n"
        "    manifest = Path('notes.md')\n    return manifest.read_text()",
        "from pathlib import Path\nmanifest = Path('workspace.yaml')\n"
        "def read(manifest=Path('notes.md')):\n    return manifest.read_text()",
    ],
)
def test_manifest_default_aliases_preserve_normal_shadowing(source):
    assert reads(source) == []


OPEN_ALIASES = (
    ("import io\nio.open('ordinary.yaml', encoding='utf-8')", False),
    ("import io as streams\nstreams.open(file='ordinary.yaml', encoding='utf-8')", False),
    ("from io import open as reader\nreader('ordinary.yaml', encoding='utf-8')", False),
    ("import builtins as basic\nbasic.open(file='ordinary.yaml', encoding='utf-8')", False),
    ("import io\nx = io.open('workspace.yaml', encoding='utf-8')", True),
    ("import io as streams\nx = streams.open(file='workspace.yaml', encoding='utf-8')", True),
    ("from io import open\nx = open('workspace.yaml', encoding='utf-8')", True),
    ("from io import open as reader\nx = reader(file='workspace.yaml', encoding='utf-8')", True),
    ("x = open(file='workspace.yaml', encoding='utf-8')", True),
    ("import builtins as basic\nx = basic.open(file='workspace.yaml', encoding='utf-8')", True),
    ("from builtins import open as reader\nx = reader('workspace.yaml', encoding='utf-8')", True),
    ("import builtins\nx = builtins.open('workspace.yaml')", True),
    ("x = open()", False),
)


@pytest.mark.parametrize("source,forbidden", OPEN_ALIASES)
def test_open_aliases(source, forbidden):
    assert bool(reads(source)) is forbidden


def test_relative_imports_resolve_against_the_package():
    scan = scan_imports("from . import x\nfrom ..y import z", module="pkg.sub.mod")
    assert scan.targets == ("pkg.sub", "pkg.sub.x", "pkg.y", "pkg.y.z")
    assert scan.raw == ("x", "y", "y.z")


def test_package_init_resolves_relative_to_itself():
    assert scan_imports("from . import x", module="pkg.sub.__init__").targets == ("pkg.sub", "pkg.sub.x")


def test_unresolvable_relative_import_reports_text_and_raw_names():
    scan = scan_imports("from ...gw_kernel import resolve", module="gw_wiki.query.injected")
    assert scan.unresolved == ("from ...gw_kernel import resolve",)
    assert scan.raw == ("gw_kernel", "gw_kernel.resolve")
    assert scan.targets == ()
    assert scan.imports[0].resolved is False and scan.imports[0].line == 1


def test_aliases_and_ancestor_projections():
    scan = scan_imports(
        "import gw_pipeline as p\np.testing.forced_tier('x')\nimport a.b\nfrom c import d as e", module="m.n"
    )
    assert scan.aliases == {"p": "gw_pipeline", "a": "a", "e": "c.d"}
    assert "gw_pipeline.testing.forced_tier" in scan.projections
    assert scan.imports[1].targets == ("a.b",)


def test_discovery_lookups_match_call_and_subscript_text():
    tree = ast.parse("import os\nos.environ['GRAPH_WORKS_DIR']\nos.getenv('GRAPH_WORKS_DIR')\nkey = 'GRAPH_WORKS_DIR'")
    assert [(f.line, f.text) for f in discovery_lookups(tree)] == [
        (2, "os.environ['GRAPH_WORKS_DIR']"),
        (3, "os.getenv('GRAPH_WORKS_DIR')"),
    ]


def test_module_name_and_under(tmp_path):
    assert module_name(tmp_path / "a/b/__init__.py", tmp_path) == "a.b.__init__"
    assert module_name(tmp_path / "x/y.py", tmp_path / "x", package="gw_work") == "gw_work.y"
    assert under("a.b.c", "a.b") and under("a.b", "a.b") and not under("a.bc", "a.b")


def test_manifest_read_findings_carry_lines():
    found = manifest_reads(ast.parse("x = 1\nopen('workspace.yaml')"), {})
    assert found == [Finding(line=2, text="open('workspace.yaml')")]


def test_annotated_assignment_and_global_scopes():
    source = (
        "from pathlib import Path\n"
        "manifest: Path = Path('workspace.yaml')\n"
        "bare: Path\n"
        "def read():\n    global manifest\n    import os.path\n    return manifest.read_text()\n"
    )
    assert reads(source) == ["manifest.read_text()"]


def test_nested_definitions_comprehensions_and_class_bases_keep_scopes():
    source = (
        "from pathlib import Path\n"
        "manifest = Path('workspace.yaml')\n"
        "class Base:\n    pass\n"
        "class Child(Base):\n    pass\n"
        "def outer():\n"
        "    def inner():\n        pass\n"
        "    class Local:\n        pass\n"
        "    values = [x for x in range(2)]\n"
        "    return lambda: manifest.read_text()\n"
    )
    assert reads(source) == ["manifest.read_text()"]


def test_scan_ignores_non_name_attribute_bases_and_star_imports():
    scan = scan_imports("import os\nf().x\nfrom os import *", module="m.n")
    assert scan.projections == ()
    assert scan.targets == ("os", "os", "os.*")


TUPLES = (
    # (assignment, read, forbidden)
    ('a, b = Path("workspace.yaml"), x', "a", True),
    ('a, b = Path("workspace.yaml"), x', "b", False),
    ('[a, b] = [Path("workspace.yaml"), x]', "a", True),
    ("a, b = rows", "a", True),
    ("a, b = rows", "b", True),
    ('a, (b, c) = Path("n.md"), (Path("workspace.yaml"), x)', "b", True),
    ('a, (b, c) = Path("n.md"), (Path("workspace.yaml"), x)', "c", False),
    ('a, (b, c) = Path("n.md"), (Path("workspace.yaml"), x)', "a", False),
    ('a, *b = Path("workspace.yaml"), x', "b", True),
    ('a, b = Path("workspace.yaml"), x, y', "b", True),
    ('a, b = rows\na, b = Path("n.md"), x', "a", False),
    ('a = rows\na, b = a, Path("n.md")', "a", True),
    ('a = rows\na, b = a, Path("n.md")', "b", False),
    ('a = b = Path("workspace.yaml")', "b", True),
    ('def f():\n    a, b = Path("workspace.yaml"), x\n    return a.read_text()\nb = 1', None, True),
)


@pytest.mark.parametrize("assignment,read,forbidden", TUPLES)
def test_tuple_assignment_targets_are_tainted_per_position(assignment, read, forbidden):
    source = f"from pathlib import Path\nrows = [Path('workspace.yaml')]\n{assignment}\n"
    if read is not None:
        source += f"{read}.read_text()\n"
    assert bool(reads(source)) is forbidden


def test_attribute_assignment_targets_are_not_tracked():
    assert reads("a, b = 1, 2\nobj.attr = 3\n") == []


STATEMENT_BINDINGS = (
    # (source, forbidden)
    ('for m in [Path("workspace.yaml")]:\n    m.read_text()', True),
    ('for m in [Path("n.md")]:\n    m.read_text()', False),
    ('for a, b in [(Path("workspace.yaml"), x)]:\n    a.read_text()', True),
    ('for a, b in [(Path("workspace.yaml"), x)]:\n    b.read_text()', False),
    ("for m in rows:\n    m.read_text()", True),
    ('m = Path("workspace.yaml")\nfor m in [Path("n.md")]:\n    pass\nm.read_text()', False),
    ('async def f():\n    async for m in [Path("workspace.yaml")]:\n        m.read_text()', True),
    ('with Path("workspace.yaml") as m:\n    m.read_text()', True),
    ('with Path("n.md") as m:\n    m.read_text()', False),
    ('with Path("n.md") as a, Path("workspace.yaml") as b:\n    b.read_text()', True),
    ('with Path("n.md") as a, Path("workspace.yaml") as b:\n    a.read_text()', False),
    ('async def f():\n    async with Path("workspace.yaml") as m:\n        m.read_text()', True),
    ('if (m := Path("workspace.yaml")):\n    m.read_text()', True),
    ('if (m := Path("n.md")):\n    m.read_text()', False),
    ('(m := Path("workspace.yaml")).read_text()', True),
)


@pytest.mark.parametrize("source,forbidden", STATEMENT_BINDINGS)
def test_for_with_and_walrus_bindings_are_tainted(source, forbidden):
    assert bool(reads(f"from pathlib import Path\nrows = [Path('workspace.yaml')]\n{source}\n")) is forbidden
