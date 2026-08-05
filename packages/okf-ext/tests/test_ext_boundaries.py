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

#: The shared layer. Everything else is a capability.
SHARED = {"__init__.py", "context.py"}


def capability_modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.relative_to(SRC).parts[0] not in SHARED)


def module_id(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


@pytest.fixture(scope="module")
def modules() -> list[Path]:
    found = capability_modules()
    assert found, "no capability modules found; the layout moved"
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
    raise AssertionError(
        "no pyproject.toml with a [tool.importlinter] section found above this file"
    )


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


def test_no_capability_imports_the_top_level_package(modules: list[Path]) -> None:
    """It would invert the re-export direction and make every capability load
    every other — the exact thing that turns option C into option A.

    Only the top-level package name is forbidden. `from okf_ext.context import
    X` is a legal import of a sibling shared module and must not be flagged —
    the rule is about the ancestor package, not its submodules.
    """
    offenders: list[str] = []
    for path in modules:
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


def test_no_module_uses_a_relative_import(modules: list[Path]) -> None:
    """A relative import hides which package a name came from, which is what
    makes a boundary check readable in the first place. okf-io is absolute
    throughout; okf-ext matches it."""
    offenders = [
        f"{module_id(path)}:{node.lineno}"
        for path in modules
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
    for shared_name in sorted(SHARED):
        path = SRC / shared_name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                parts = node.module.split(".")
                if len(parts) >= 2 and parts[0] == "okf_ext" and parts[1] in names:
                    offenders.append(f"{shared_name}:{node.lineno} {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if len(parts) >= 2 and parts[0] == "okf_ext" and parts[1] in names:
                        offenders.append(f"{shared_name}:{node.lineno} import {alias.name}")
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
    missing = sorted(
        f"okf_ext.{name}" for name in capability_names(modules) if f"okf_ext.{name}" not in named
    )
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
    missing = sorted(
        f"okf_ext.{name}" for name in capability_names(modules) if f"okf_ext.{name}" not in named
    )
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


@pytest.fixture(params=["tags", "schemas"])
def capability(request):
    return importlib.import_module(f"okf_ext.{request.param}")


def test_every_capability_on_disk_is_covered_by_these_tests(modules: list[Path]) -> None:
    """The `capability` fixture is a literal list, unlike `capability_names`.
    This is what stops a third capability from being added without anyone
    extending the surface tests below."""
    assert capability_names(modules) == {"tags", "schemas"}


def test_all_lists_exactly_what_the_module_exports(capability) -> None:
    """A stale `__all__` is a public surface that lies, and an unsorted one
    would be silently reordered out from under this test by `ruff check
    --fix` (`RUF022`) the next time someone runs it."""
    assert list(capability.__all__) == sorted(
        capability.__all__, key=lambda name: (_ruf022_group(name), name)
    )
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
    """Spec §5 of the schemas work item: two functions, two values, two codes."""
    from okf_ext import schemas

    assert callable(schemas.load_schemas)
    assert callable(schemas.schema_rule)
    assert schemas.TOPIC == "schemas"
    assert schemas.CODES == ("schemas.invalid", "schemas.no-schema-for-type")
    assert schemas.DEFAULT_SCHEMA_DIRNAME == "_schema"
    assert schemas.DEFAULT_IGNORE == ("_schema/*", "*/_schema/*")


def test_no_capability_claims_a_built_in_topic_prefix() -> None:
    """Every capability's claim on a topic prefix, checked in one place."""
    from okf_ext import schemas, tags

    claimed = {tags.TOPIC, schemas.TOPIC}
    assert len(claimed) == 2, "two capabilities claiming one prefix"
    assert not (claimed & BUILT_IN_TOPICS)
