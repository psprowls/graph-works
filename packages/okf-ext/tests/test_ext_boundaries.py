"""The half of the internal boundary import-linter cannot express, plus a
check that keeps import-linter's own contract honest as capabilities grow.

Verified against import-linter 2.13: grimp does not report an import of an
*ancestor* package as a dependency, so a `forbidden` contract with
`source_modules = ["okf_ext.tags"]` and `forbidden_modules = ["okf_ext"]`
reports KEPT even when `okf_ext/tags/model.py` literally contains
`from okf_ext import ExtContext`. The layers contract in the root
`pyproject.toml` covers the other half — the shared layer never importing a
capability — and that one does bite.

But a `layers` contract only checks the layers it was told to enumerate: it
cannot discover a new capability on its own, so a second capability added
without a matching edit to `pyproject.toml` is invisible to `uv run
lint-imports` — it reports the same `1 kept, 0 broken` either way. Every
capability set in this file is therefore derived from the filesystem
(`capability_names`), never hardcoded, and `capability_names` itself is
computed *from* the `modules` fixture the other tests already use rather
than from an independent walk — a second, drifting source of truth is
exactly what let a fabricated `okf_ext.othercap` capability import from the
shared layer past both `test_the_shared_layer_imports_no_capability`
(before it was fixed) and `uv run lint-imports` at the same time.
"""

from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "okf_ext"

#: The shared layer. Everything else is a capability. `shape` is a package
#: rather than a single module, which is why `shared_modules()` expands a
#: directory entry instead of assuming every name here is a file.
SHARED = {"__init__.py", "body.py", "context.py", "shape", "splice.py", "writing.py"}


def shared_modules() -> list[Path]:
    """Every source file in the shared layer, packages expanded."""
    found: list[Path] = []
    for name in sorted(SHARED):
        target = SRC / name
        found.extend(sorted(target.rglob("*.py")) if target.is_dir() else [target])
    return found


def capability_modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.relative_to(SRC).parts[0] not in SHARED)


def all_modules() -> list[Path]:
    """Every source module, shared layer included.

    Spec §6: "neither new module, nor `tables`, may import the top-level
    `okf_ext` package" applies to the shared modules themselves
    (`body.py`, `writing.py`, `context.py`), not only to capabilities. A
    `capability_modules()` filter would never see them, so the two AST
    checks that state this rule use this wider list instead.
    """
    return sorted(SRC.rglob("*.py"))


def module_id(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


@pytest.fixture(scope="module")
def modules() -> list[Path]:
    found = capability_modules()
    assert found, "no capability modules found; the layout moved"
    return found


@pytest.fixture(scope="module")
def source_modules() -> list[Path]:
    found = all_modules()
    assert found, "no source modules found; the layout moved"
    return found


def capability_names(modules: list[Path]) -> set[str]:
    """The set of capability package/module names, derived from the same
    filesystem walk `modules` already did — the top-level path component of
    every non-shared module under `src/okf_ext/`. A second, independent
    hardcoded list (e.g. `{"tags"}`) is exactly what let a fabricated second
    capability slip past `test_the_shared_layer_imports_no_capability`
    unnoticed: deriving it here instead means a new capability directory is
    covered with no edit to this file."""
    return {path.relative_to(SRC).parts[0] for path in modules}


def _find_repo_pyproject() -> Path:
    """Walk up from this test file to the `pyproject.toml` that carries
    `[tool.importlinter]` — not `packages/okf-ext/pyproject.toml`, which has
    none. Walking rather than hardcoding a `parents[N]` index survives the
    repo root moving relative to this test file."""
    here = Path(__file__).resolve()
    for directory in (here.parent, *here.parents):
        candidate = directory / "pyproject.toml"
        if not candidate.is_file():
            continue
        with candidate.open("rb") as handle:
            data = tomllib.load(handle)
        if "importlinter" in data.get("tool", {}):
            return candidate
    raise AssertionError("no pyproject.toml with a [tool.importlinter] section found above this file")


def _layer_names_in_contracts(pyproject_path: Path) -> set[str]:
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    contracts = data["tool"]["importlinter"].get("contracts", [])
    names: set[str] = set()
    for contract in contracts:
        for entry in contract.get("layers", []):
            # A layers entry is either one module name, a list of names, or a
            # `a : b` string naming siblings at one level. All three shapes name
            # real layers and all three count -- treating the third as one
            # opaque string is how a capability goes uncovered while the test
            # still passes.
            items = entry if isinstance(entry, list) else [entry]
            for item in items:
                names.update(part.strip() for part in str(item).split(":") if part.strip())
    return names


def test_no_capability_imports_the_top_level_package(source_modules: list[Path]) -> None:
    """It would invert the re-export direction and make every capability load
    every other — the exact thing that turns option C into option A.

    Only the top-level package name is forbidden. `from okf_ext.context import
    X` is a legal import of a sibling shared module and must not be flagged —
    the rule is about the ancestor package, not its submodules.

    Covers the shared layer too (`body.py`, `writing.py`, `context.py`), not
    only capabilities: spec §6 states the rule for "neither new module, nor
    `tables`", and this is the only check that can enforce it there --
    `import-linter` provably cannot (see this module's docstring), and a
    `from okf_ext import ExtContext` inside a shared module would import
    cleanly at runtime.
    """
    offenders: list[str] = []
    for path in source_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "okf_ext":
                offenders.append(f"{module_id(path)}:{node.lineno} from okf_ext import ...")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{module_id(path)}:{node.lineno} import {alias.name}"
                    for alias in node.names
                    if alias.name == "okf_ext"
                )
    assert not offenders, "\n".join(offenders)


def test_no_module_uses_a_relative_import(source_modules: list[Path]) -> None:
    """A relative import hides which package a name came from, which is what
    makes a boundary check readable in the first place. okf-io is absolute
    throughout; okf-ext matches it.

    Covers the shared layer too, alongside every capability -- the property
    is not capability-specific, and `okf_ext/__init__.py`'s own
    `from okf_ext.context import ...` is an absolute import of a sibling
    module, not a relative one, so widening the module list to include it
    does not introduce a false positive.
    """
    offenders = [
        f"{module_id(path)}:{node.lineno}"
        for path in source_modules
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert not offenders, "\n".join(offenders)


def test_the_shared_layer_imports_no_capability(modules: list[Path]) -> None:
    """The layers contract asserts this too. Asserted here as well so a broken
    boundary fails the test suite, not only the opt-in `just contracts`.

    The capability set comes from `capability_names(modules)` — the same
    filesystem walk the other two tests already use — rather than a second,
    independent literal like `{"tags"}`. A hardcoded name here is exactly
    what let a fabricated `okf_ext.othercap` capability slip past this test
    (and past the `layers` contract's own blind spot, which only checks the
    layers it was told to name — see the sibling test below) even though
    `context.py` plainly imported from it.
    """
    names = capability_names(modules)
    offenders: list[str] = []
    for path in shared_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                parts = node.module.split(".")
                if len(parts) >= 2 and parts[0] == "okf_ext" and parts[1] in names:
                    offenders.append(f"{module_id(path)}:{node.lineno} {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if len(parts) >= 2 and parts[0] == "okf_ext" and parts[1] in names:
                        offenders.append(f"{module_id(path)}:{node.lineno} import {alias.name}")
    assert not offenders, "\n".join(offenders)


def test_every_capability_is_named_in_the_layers_contract(modules: list[Path]) -> None:
    """A `layers` contract can only check the layers it was told to enumerate
    — `import-linter` never discovers capabilities on its own. That means
    forgetting to add a new `okf_ext.<capability>` to
    `pyproject.toml`'s `[[tool.importlinter.contracts]]` is a silent hole:
    `uv run lint-imports` reports `1 kept, 0 broken` whether or not the new
    capability is actually covered, because "covered" and "not mentioned"
    both print the same KEPT. Nothing but this test notices the gap; it is
    the filesystem-vs-contract check that closes the blind spot the
    fabricated `okf_ext.othercap` probe walked straight through.
    """
    pyproject_path = _find_repo_pyproject()
    named = _layer_names_in_contracts(pyproject_path)
    missing = sorted(f"okf_ext.{name}" for name in capability_names(modules) if f"okf_ext.{name}" not in named)
    assert not missing, (
        f"{missing} not named in any `layers` contract in {pyproject_path}; "
        "a layers contract only checks the layers it enumerates, so an unlisted "
        "capability is invisible to `just contracts` until someone adds it here"
    )


def _independence_modules_in_contracts(pyproject_path: Path) -> set[str]:
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    contracts = data["tool"]["importlinter"].get("contracts", [])
    names: set[str] = set()
    for contract in contracts:
        if contract.get("type") == "independence":
            names.update(str(item) for item in contract.get("modules", []))
    return names


def test_every_capability_is_named_in_the_independence_contract(modules: list[Path]) -> None:
    """The `layers` contract's blind spot has a twin in `independence`: it too
    only checks the modules it was told to enumerate, so a capability present
    in the `layers` entry but missing from `independence.modules` would pass
    `lint-imports` (`2 kept, 0 broken`) while a real cross-capability import
    between it and a sibling goes undetected. Checked separately from the
    `layers` contract above because the two use different TOML keys
    (`layers` vs. `modules`) and a capability could drift out of either one
    independently of the other.
    """
    pyproject_path = _find_repo_pyproject()
    named = _independence_modules_in_contracts(pyproject_path)
    missing = sorted(f"okf_ext.{name}" for name in capability_names(modules) if f"okf_ext.{name}" not in named)
    assert not missing, (
        f"{missing} not named in the `independence` contract's `modules` in "
        f"{pyproject_path}; an unlisted capability's imports of its siblings "
        "go unchecked by `lint-imports` even though the contract reports KEPT"
    )


def _ruf022_group(name: str) -> int:
    """`ruff`'s `RUF022` groups `__all__` into UPPER_SNAKE_CASE constants,
    then CapWords classes, then everything else, each group alphabetical —
    verified empirically against ruff by round-tripping small probe lists
    through `ruff check --select RUF022 --fix`. Plain `sorted()` does *not*
    match this: Python's code-point order puts every uppercase name ahead of
    every lowercase one, which is a different order (`ApplyResult` before
    `CODES`) than the grouping `ruff` and this module both use."""
    if name.isupper():
        return 0
    if name[:1].isupper():
        return 1
    return 2


#: The eight topic prefixes okf-io's own catalog claims. Restated here rather
#: than imported from `okf_io._rules`: nothing outside that package should reach
#: into its underscore modules, and okf-io raises at runtime anyway the moment an
#: external rule emits a colliding prefix. This says so up front instead of
#: waiting for a bundle to trigger it.
BUILT_IN_TOPICS = frozenset(
    {
        "computation",
        "frontmatter",
        "legacy",
        "lifecycle",
        "links",
        "provenance",
        "reserved",
        "trust",
    }
)


@pytest.fixture(
    params=[
        "tags",
        "schemas",
        "render",
        "health",
        "search",
        "tables",
        "moves",
        "sections",
        "generators",
        "proposals",
        "bundle",
        "placement",
    ]
)
def capability(request):
    return importlib.import_module(f"okf_ext.{request.param}")


def test_every_capability_on_disk_is_covered_by_these_tests(modules: list[Path]) -> None:
    """The `capability` fixture is a literal list, unlike `capability_names`.
    This is what stops a fourth capability from being added without anyone
    extending the surface tests below."""
    assert capability_names(modules) == {
        "tags",
        "schemas",
        "render",
        "health",
        "search",
        "tables",
        "moves",
        "sections",
        "generators",
        "proposals",
        "bundle",
        "placement",
    }


def test_all_lists_exactly_what_the_module_exports(capability) -> None:
    """A stale `__all__` is a public surface that lies, and an unsorted one
    would be silently reordered out from under this test by `ruff check
    --fix` (`RUF022`) the next time someone runs it."""
    assert list(capability.__all__) == sorted(capability.__all__, key=lambda name: (_ruf022_group(name), name))
    for name in capability.__all__:
        assert hasattr(capability, name), f"__all__ names {name}, which does not exist"


def test_every_name_in_all_is_individually_importable(capability) -> None:
    """`hasattr` alone would pass for a name that only resolves as an
    attribute of the already-imported module object, which is not the same
    claim as "this name is importable"."""
    for name in capability.__all__:
        namespace: dict[str, object] = {}
        exec(f"from {capability.__name__} import {name} as _value", namespace)
        assert namespace["_value"] is getattr(capability, name)


def test_the_documented_tags_surface_is_present() -> None:
    """Spec §12.4 of the tags work item."""
    from okf_ext import tags

    for name in (
        "canonical",
        "inventory",
        "clusters",
        "load_vocabulary",
        "vocabulary_rule",
        "plan_rename",
        "plan_merge",
        "plan_normalize",
        "plan_from_vocabulary",
        "apply",
    ):
        assert callable(getattr(tags, name))


def test_the_documented_schemas_surface_is_present() -> None:
    """Spec §5 of the schemas work item: two functions, two values, two codes.
    `declared_directories` is the third function, added for `okf_ext.placement`
    to compose against -- the capability may not import this one."""
    from okf_ext import schemas

    assert callable(schemas.load_schemas)
    assert callable(schemas.schema_rule)
    assert callable(schemas.declared_directories)
    assert schemas.TOPIC == "schemas"
    assert schemas.CODES == ("schemas.invalid", "schemas.no-schema-for-type")
    assert schemas.DEFAULT_SCHEMA_DIRNAME == "_schema"
    assert schemas.DEFAULT_IGNORE == ("_schema/*", "*/_schema/*")


def test_the_documented_render_surface_is_present() -> None:
    """Spec §3 and §6.3 of the render work item: one factory, one helper, four codes."""
    from okf_ext import render

    assert callable(render.render_rule)
    assert callable(render.escape_angle_brackets)
    assert render.TOPIC == "render"
    assert render.CODES == (
        "render.angle-bracket",
        "render.callout",
        "render.wikilink",
        "render.table-pipe",
    )


def test_the_documented_health_surface_is_present() -> None:
    """Spec §3 and §7 of the health work item: one factory, three codes."""
    from okf_ext import health

    assert callable(health.health_rule)
    assert health.TOPIC == "health"
    assert health.CODES == ("health.uncited", "health.duplicate-title", "health.log-gap")


def test_the_documented_search_surface_is_present() -> None:
    """Spec §3 of the search work item: five functions, three values, and
    deliberately no `TOPIC` — the first capability that reports nothing."""
    from okf_ext import search

    for name in ("bm25_scores", "build_index", "search", "snippet", "tokenize"):
        assert callable(getattr(search, name))
    assert search.DEFAULT_WEIGHTS == {"title": 3, "description": 2, "tags": 2, "body": 1}
    assert search.TOKEN_RE.pattern == r"[a-zA-Z0-9][a-zA-Z0-9_\-']+"
    assert isinstance(search.STOPWORDS, frozenset)
    assert not hasattr(search, "TOPIC"), "search answers questions; it files no findings"
    assert not hasattr(search, "CODES")


def test_no_capability_claims_a_built_in_topic_prefix(modules: list[Path]) -> None:
    """Every capability's claim on a topic prefix, checked in one place.

    The capability set is derived from the filesystem rather than hardcoded as
    a literal tuple of capability modules. A hardcoded list does not fail when
    a further capability lands — it simply stops covering the package, which is
    the same silent blind spot the two contract-coverage tests above were
    written to close.

    A capability with **no** `TOPIC` is legal and is skipped, not failed.
    `search` and `tables` both claim none: search answers questions rather
    than filing `Finding`s, and tables is a primitive rules are built on, not
    a rule itself. Neither needs an entry in okf-io's code namespace. The
    assertion is therefore *every capability that defines `TOPIC` claims a
    prefix that is distinct and outside okf-io's built-in eight*, rather than
    *every capability defines one*.
    """
    claimed = [
        topic
        for topic in (
            getattr(importlib.import_module(f"okf_ext.{name}"), "TOPIC", None)
            for name in sorted(capability_names(modules))
        )
        if topic is not None
    ]
    assert claimed, "no capability claims a topic prefix at all; the layout moved"
    assert len(claimed) == len(set(claimed)), f"two capabilities claiming one prefix: {claimed}"
    assert not (set(claimed) & BUILT_IN_TOPICS)


def test_the_documented_tables_surface_is_present() -> None:
    """Spec §5 of the tables work item: two read levels, the text splice, and
    the bundle pair."""
    from okf_ext import tables

    for name in ("read", "read_all", "read_section", "splice_text", "plan_row", "apply"):
        assert callable(getattr(tables, name))


def test_the_documented_body_surface_is_present() -> None:
    """Spec §5.1. `okf_ext.body` is shared, so it is not covered by the
    `capability` fixture -- asserted here so the surface is pinned somewhere."""
    from okf_ext import body

    for name in ("sections", "find_section", "prose_lines", "split_lines"):
        assert callable(getattr(body, name))
    assert body.Section.__dataclass_fields__.keys() == {"heading", "level", "start", "body_start", "stop"}


def test_the_documented_writing_surface_is_present() -> None:
    """`okf_ext.writing` is shared, so it is not covered by the `capability`
    fixture -- asserted here for the same reason `body`'s surface is pinned
    above. It is the module whose names `okf_ext.tags` re-exports, so an
    accidental rename here breaks a shipped capability with nothing else in
    the suite to catch it."""
    from okf_ext import writing

    assert callable(writing.write_all)
    assert writing.PendingWrite.__dataclass_fields__.keys() == {
        "member",
        "path",
        "rendered",
        "on_written",
        "create",
    }


def test_the_documented_moves_surface_is_present() -> None:
    """Spec §4 of the moves work item: four planners, one `apply`."""
    from okf_ext import moves

    for name in ("plan_move", "plan_move_dir", "plan_move_many", "plan_repair", "apply"):
        assert callable(getattr(moves, name))
    assert moves.REFERENCE_KEYS[0] == "resource"


def test_the_tables_capability_claims_no_topic_prefix() -> None:
    """`tables` is a primitive lint rules are built on, not a rule itself: it
    emits no `Finding`, so it claims no topic and exports no codes. The
    `rules` item is where `tables`-derived findings will get their topic.
    Asserted rather than left implicit, because adding a `TOPIC` here would
    also need adding to `test_no_capability_claims_a_built_in_topic_prefix`'s
    `claimed` set, and a silent omission there is exactly the collision that
    check exists to catch."""
    from okf_ext import tables

    assert not hasattr(tables, "TOPIC")
    assert not hasattr(tables, "CODES")


def test_the_documented_sections_surface_is_present() -> None:
    """Spec §4, §5 and §6 of the sections work item: one loader, one rule
    factory, one pure renderer, and the plan/apply pair."""
    from okf_ext import sections

    for name in ("load_sections", "section_rule", "render_skeleton", "plan_sections", "apply"):
        assert callable(getattr(sections, name))
    assert sections.TOPIC == "sections"
    assert sections.CODES == (
        "sections.missing",
        "sections.unfilled",
        "sections.unexpected",
        "sections.no-declaration-for-type",
    )
    assert sections.DEFAULT_SECTIONS_DIRNAME == "_sections"
    assert sections.SECTION_SUFFIXES == (".yaml", ".yml")
    assert sections.DEFAULT_IGNORE == ("_sections/*", "*/_sections/*")


def test_the_documented_splice_surface_is_present() -> None:
    """`okf_ext.splice` is shared, so it is not covered by the `capability`
    fixture -- pinned here for the same reason `body`'s and `writing`'s
    surfaces are. `tables` and `sections` both depend on these five, so a
    rename here breaks two shipped capabilities at once."""
    from okf_ext import splice

    for name in ("assemble", "dominant_newline", "has_trailing_newline", "insert", "needs_gap"):
        assert callable(getattr(splice, name))
    assert (splice.CRLF, splice.LF, splice.CR) == ("\r\n", "\n", "\r")
    assert splice.TERMINATORS == ("\n", "\r")


def test_the_documented_shape_surface_is_present() -> None:
    """`okf_ext.shape` is shared, so it is not covered by the `capability`
    fixture -- pinned here for the same reason `body`'s, `writing`'s and
    `splice`'s surfaces are. `sections` and `generators` both read these
    types, so a rename here breaks two capabilities at once."""
    from okf_ext import shape

    assert callable(shape.load_sections)
    assert shape.DEFAULT_SECTIONS_DIRNAME == "_sections"
    assert shape.SECTION_SUFFIXES == (".yaml", ".yml")
    assert shape.DEFAULT_IGNORE == ("_sections/*", "*/_sections/*")
    assert shape.SectionSpec.__dataclass_fields__.keys() == {
        "heading",
        "level",
        "required",
        "seeded_is_complete",
        "placeholder",
        "ownership",
    }
    assert shape.TypeSections.__dataclass_fields__.keys() == {"sections", "additional_sections", "frontmatter"}
    assert shape.FrontmatterOwnership.__dataclass_fields__.keys() == {"owned", "provenance"}
    assert list(shape.__all__) == sorted(shape.__all__, key=lambda name: (_ruf022_group(name), name))


def test_the_documented_generators_surface_is_present() -> None:
    """Spec §5 of the generators work item: one planner, one `apply`, and the
    two pure halves the properties test directly.

    Field sets pinned the same way `test_the_documented_shape_surface_is_present`
    pins its three dataclasses, for the same reason: these are the
    capability's public value types, and a rename on any of them is a
    breaking change for a consumer that nothing else in the suite catches."""
    from okf_ext import generators

    for name in ("plan_regenerate", "apply", "key_edits", "regenerate_body"):
        assert callable(getattr(generators, name))
    assert generators.Render.__dataclass_fields__.keys() == {"frontmatter", "sections"}
    assert generators.Render().frontmatter == {}
    assert generators.Render().sections == {}
    assert generators.KeyEdit.__dataclass_fields__.keys() == {"key", "action", "value"}
    assert generators.SectionEdit.__dataclass_fields__.keys() == {"heading", "line"}
    assert generators.Regeneration.__dataclass_fields__.keys() == {
        "concept_id",
        "path",
        "key_edits",
        "section_edits",
        "digest",
        "after",
    }
    assert generators.RegenerationPlan.__dataclass_fields__.keys() == {"root", "regenerations", "skipped"}


def test_the_generators_capability_claims_no_topic_prefix() -> None:
    """A primitive, exactly as `tables` is: a capability that both writes
    documents *and* judges them would make every future rule import the
    writer through a rule module. The seeding axis is already covered by
    `sections.missing` and `sections.unfilled`, which read the same
    declaration."""
    from okf_ext import generators

    assert not hasattr(generators, "TOPIC")
    assert not hasattr(generators, "CODES")


def test_the_documented_proposals_surface_is_present() -> None:
    """Spec §4 of the proposals work item: four planners, two readers, one
    `apply`, and the pure renderer the determinism property tests directly."""
    from okf_ext import proposals

    for name in (
        "plan_propose",
        "plan_decide",
        "plan_create",
        "plan_promote",
        "list_proposals",
        "mode",
        "apply",
        "render_body",
    ):
        assert callable(getattr(proposals, name))
    assert proposals.PROPOSAL_TYPE == "Proposal"
    assert proposals.PAGE_STATUSES == ("proposed", "approved", "rejected", "created")
    assert proposals.OWNED_PROVENANCE_KEYS == ("generated", "sources", "verified")
    assert proposals.Proposal.__dataclass_fields__.keys() == {
        "member",
        "concept_id",
        "target",
        "title",
        "description",
        "page_status",
        "raw_page_status",
        "sources",
        "verified",
        "malformed",
    }
    assert proposals.Write.__dataclass_fields__.keys() == {"member", "mode", "text", "frontmatter", "body", "digest"}
    assert proposals.PageRender.__dataclass_fields__.keys() == {"type", "body", "frontmatter"}


def test_the_proposals_capability_claims_no_topic_prefix() -> None:
    """A primitive, exactly as `tables` and `generators` are. `page_status` is
    an extension key and a proposal carries no OKF `status`, so adopting the
    capability adds no finding -- the failure a tier-2 ledger must not cause."""
    from okf_ext import proposals

    assert not hasattr(proposals, "TOPIC")
    assert not hasattr(proposals, "CODES")


def test_the_documented_bundle_surface_is_present() -> None:
    """Spec S-A of the co-existence work item: two planners, one `apply`, and
    the routing constants a caller relocating its declarations needs to read."""
    from okf_ext import bundle

    for name in ("plan_scaffold", "plan_install", "apply"):
        assert callable(getattr(bundle, name))
    assert bundle.DECLARATION_PREFIXES == ("_schema/", "_sections/")
    assert bundle.DECLARATION_MEMBERS == ("_tags.yaml",)
    assert bundle.PlannedFile.__dataclass_fields__.keys() == {"member", "path", "content"}
    assert bundle.ScaffoldPlan.__dataclass_fields__.keys() == {
        "root",
        "declarations_dir",
        "writes",
        "skipped",
        "refusals",
    }
    assert bundle.InstallPlan.__dataclass_fields__.keys() == bundle.ScaffoldPlan.__dataclass_fields__.keys()


def test_the_bundle_capability_claims_no_topic_prefix() -> None:
    """A primitive, as `tables`, `generators` and `proposals` are. Setting a
    bundle up is not a judgement about it; the rules that judge one are the
    caller's to run."""
    from okf_ext import bundle

    assert not hasattr(bundle, "TOPIC")
    assert not hasattr(bundle, "CODES")


def test_the_documented_placement_surface_is_present() -> None:
    """Spec §3 of the curated-lane work item: one factory, two codes, and no
    vocabulary -- the capability exports no lane map of its own, because the
    map is the caller's."""
    from okf_ext import placement

    assert callable(placement.placement_rule)
    assert placement.TOPIC == "placement"
    assert placement.CODES == ("placement.directory-mismatch", "placement.duplicate-resource")
    assert list(placement.__all__) == ["CODES", "TOPIC", "placement_rule"]


def test_the_shape_surface_pins_the_index_dispatch_table() -> None:
    """`SectionSet` gained a second dispatch table, and it is defaulted so a
    caller constructing one without it still works. Both halves of that are
    contract, not implementation."""
    from okf_ext import shape

    assert shape.SectionSet.__dataclass_fields__.keys() == {"types", "sources", "fragments", "root", "indexes"}
    assert shape.SectionSet(types={}, sources={}, fragments={}, root=Path()).indexes == {}


def test_plan_regenerate_accepts_index_renders() -> None:
    """A separate parameter rather than an overload of `renders`, because a
    directory id and a concept id genuinely collide: `packages` the lane and
    `packages.md` the concept share a key."""
    import inspect

    from okf_ext import generators

    parameters = inspect.signature(generators.plan_regenerate).parameters
    assert "index_renders" in parameters
    assert parameters["index_renders"].default == {}
