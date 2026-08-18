"""The packaged role catalog: ten roles, no sweeps, readable as package data."""

from __future__ import annotations

import ast
import tomllib
from importlib import resources
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_core"
_BINDERS = {"make_llm", "role_binding", "role_spec"}

EXPECTED_ROLES = [
    "librarian",
    "code_reader",
    "linter",
    "ingestor",
    "proposal_reasoner",
    "prose_refresher",
    "query_orchestrator",
    "synthesizer",
    "extractor",
    "drift_propagator",
]


def _catalog():
    with resources.files("graph_works_core").joinpath("models.toml").open("rb") as handle:
        return tomllib.load(handle)


def _roles_bound_in_src() -> set[str]:
    """Every role name `src/` binds, collected from the four shapes it uses.

    1. a string-literal first argument to make_llm / role_binding / role_spec;
    2. a module-level `*_ROLE = "..."` constant;
    3. the `ALLOWED_WORKERS` tuple, which `role_binding(role)` iterates;
    4. a `role = "..."` class attribute on an adapter.

    Shape 4 binds nothing today — the adapters use `role` to label a
    `LoopOutcome` — but the predecessor repo's `subagent_cli/runner.py`
    resolves models through exactly it, so an adapter naming a role no call
    site binds is a reachable next state.

    A fifth shape added later is missed silently. That is the same class of gap
    this test closes, and the reason to extend the list here rather than to
    write the bound set down by hand somewhere else.
    """
    found: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # Shape 1
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _BINDERS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
            # Shapes 2, 3 and 4 are all assignments
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    # Shape 2
                    if (
                        target.id.endswith("_ROLE")
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        found.add(node.value.value)
                    # Shape 3
                    if target.id == "ALLOWED_WORKERS" and isinstance(node.value, ast.Tuple):
                        found.update(
                            element.value
                            for element in node.value.elts
                            if isinstance(element, ast.Constant) and isinstance(element.value, str)
                        )
                    # Shape 4
                    if (
                        target.id == "role"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        found.add(node.value.value)
    return found


def test_the_catalog_is_readable_as_package_data():
    assert "roles" in _catalog()


def test_the_catalog_carries_exactly_the_rebuild_roles():
    assert sorted(_catalog()["roles"]) == sorted(EXPECTED_ROLES)


def test_no_role_carries_sweep_candidates():
    for name, entry in _catalog()["roles"].items():
        assert "sweep_candidates" not in entry, name


def test_every_role_is_fully_specified():
    for name, entry in _catalog()["roles"].items():
        assert set(entry) == {"model_id", "region", "max_tokens", "max_concurrency"}, name


def test_the_catalog_and_the_bindings_agree():
    # The defect this closes, seen from both sides: `drift_propagator` was
    # bound by `commands/propagate_drift.py` and absent from the catalog, so
    # drift propagation no-opped on every run; `preflight` and `scanner` sat
    # in the catalog with nothing binding them. One equality catches both, and
    # two one-directional tests would be this same list written twice.
    bound = _roles_bound_in_src()
    declared = set(_catalog()["roles"])
    assert bound == declared, {
        "bound but not catalogued": sorted(bound - declared),
        "catalogued but not bound": sorted(declared - bound),
    }


def test_no_declared_role_appears_in_the_headers_omitted_list():
    # The bug this replaces a hand-written reconciliation for: the header
    # listed `drift_propagator` as unported while the code bound it, and
    # listed `synthesizer` as unported while the table declared it. Checking
    # every declared name at once means the next divergence cannot hide
    # behind a role nobody thought to write a test for.
    #
    # Every parenthetical in the header is swept in, including the tuning-session
    # date. Harmless — no role is named `2026-05-30` — but worth knowing before
    # you add a parenthetical of your own.
    text = resources.files("graph_works_core").joinpath("models.toml").read_text(encoding="utf-8")
    header = text.split("[roles.", 1)[0]
    flat_header = " ".join(line.lstrip("#").strip() for line in header.splitlines())
    omitted = set()
    for chunk in flat_header.split("(")[1:]:
        omitted.update(name.strip() for name in chunk.split(")", 1)[0].split(","))
    declared = set(_catalog()["roles"])
    assert omitted & declared == set(), sorted(omitted & declared)
