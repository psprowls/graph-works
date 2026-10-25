"""The extracted work distribution preserves its public identities."""

import importlib
from importlib.metadata import version
from pathlib import Path

import pytest


def test_package_shape_and_exports():
    import gw_work
    from gw_pipeline.transactions import MutationApplication, apply_mutation
    from gw_work.archive.commands import run_archive

    assert version("gw-work") == "0.2.0"
    assert gw_work.__all__ == EXPECTED
    assert gw_work.run_archive is run_archive
    assert gw_work.MutationApplication is MutationApplication
    assert gw_work.apply_mutation is apply_mutation
    assert Path(gw_work.__file__).with_name("py.typed").is_file()
    kernel = Path(__file__).resolve().parents[2] / "gw-kernel/src/gw_kernel"
    assert not (kernel / "work").exists()
    assert not (kernel / "archive").exists()


EXPECTED = [
    "ArchiveRun",
    "FilingRun",
    "IngestQueueReport",
    "ItemRead",
    "ItemSource",
    "MutationApplication",
    "NextResult",
    "PathMutationResult",
    "PendingIngest",
    "RegenIndexesResult",
    "StatusReport",
    "apply_mutation",
    "run_archive",
    "run_file",
    "run_ingest_queue",
    "run_item_read",
    "run_lint",
    "run_next",
    "run_regen_indexes",
    "run_release_adoption",
    "run_reparent",
    "run_status",
]


@pytest.mark.parametrize("name", ["ArchiveRun", "run_archive"])
def test_each_target_resolves_to_the_object_the_front_door_returns(name: str) -> None:
    import gw_work

    assert getattr(importlib.import_module("gw_work.archive.commands"), name) is getattr(gw_work, name)
