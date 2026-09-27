"""Keep the CLI's structural Orca seam and the two launch writers aligned."""

from __future__ import annotations

import typing

from graph_works_cli.work_cli.orca import orca_port
from graph_works_core.orchestrate import dispatch as core_dispatch
from graph_works_core.orchestrate import orca_port as core
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from workflow_orca import port as vendor
from workflow_orca._launch import encode_launch_spec

PAIRS = (
    "OrcaRepo",
    "OrcaWorktree",
    "OrcaTask",
    "OrcaStart",
    "OrcaWorker",
    "OrcaWorkerShow",
    "OrcaRead",
    "OrcaMessage",
    "OrcaDelivery",
)


def _shape(annotation: object) -> object:
    """Compare structural annotations without requiring class identity across packages."""
    origin = typing.get_origin(annotation)
    if origin is not None:
        return (origin, tuple(_shape(arg) for arg in typing.get_args(annotation)))
    if typing.is_typeddict(annotation):
        return annotation.__name__
    return annotation


def _hints(value: object) -> dict[str, object]:
    return {name: _shape(annotation) for name, annotation in typing.get_type_hints(value).items()}


def test_the_adapter_satisfies_the_protocol_at_runtime() -> None:
    assert isinstance(orca_port(), core.OrcaPort)


def test_every_value_type_is_mirrored_item_for_item() -> None:
    for name in PAIRS:
        assert _hints(getattr(core, name)) == _hints(getattr(vendor, name)), name


def test_the_wait_surface_is_on_both_sides() -> None:
    for name in ("check_wait", "check_ack", "run_use"):
        assert name in core.OrcaPort.__dict__, name
        assert callable(getattr(vendor.OrcaCliPort, name)), name
        assert _hints(getattr(core.OrcaPort, name)) == _hints(getattr(vendor.OrcaCliPort, name)), name


def test_core_and_vendor_encode_the_same_envelope() -> None:
    worktree = WorktreeAction(
        action="reuse", path="/w", branch="b", base_branch=None, exists=True, parent_path=None, start_sha=None
    )
    planned = PlannedDispatch(
        key="k",
        slug="work/x",
        phase="execute",
        kind="Feature",
        effort="small",
        skill="s",
        mode="attend",
        agent="claude",
        model="opus",
        reasoning_effort="high",
        worktree=worktree,
        merge_target="main",
        prompt="go\n",
        auto_merge=False,
    )
    argv = ["--worktree", "path:/w"]
    assert core_dispatch.encode_launch_envelope(
        key="k",
        agent="claude",
        model="opus",
        reasoning_effort="high",
        placement_argv=argv,
        mode="attend",
        worktree_path="/w",
        prompt="go\n",
    ) == encode_launch_spec(planned, placement_argv=argv)
