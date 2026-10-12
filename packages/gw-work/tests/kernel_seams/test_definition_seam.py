"""The child 1 / child 2 names tech-debt-core-consumers-derive-stages consumes."""

from __future__ import annotations

import inspect

from work_tracker_okf.compose import advance_and_stamp


def test_compose_and_core_take_a_definition() -> None:
    from gw_kernel.workspace.dispatch_config import DispatchConfig
    from gw_work import commands

    assert "definition" in inspect.signature(advance_and_stamp).parameters
    assert "definition" in inspect.signature(commands._plan_route).parameters
    assert isinstance(inspect.getattr_static(DispatchConfig, "definition"), property)
    assert callable(commands._load_config) and callable(commands._definition_of)
