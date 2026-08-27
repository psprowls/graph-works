"""`gw util platform`'s report: the types, the four providers, and the probes.

The property that matters most here is that a Windows report is asserted from
a POSIX box. The epic bought manual Windows verification and no CI, so
`build_report(platform_name="win32")` is the only place in this epic where
Windows-shaped behaviour is checked on the machine the work is done on.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from graph_works_core.util.platform import (
    LOCK_SITES,
    POSIX_ONLY_MODULES,
    PROVIDERS,
    SCHEMA_VERSION,
    Capability,
    DispatchBackendProvider,
    DurabilityTierProvider,
    FileLockProvider,
    PlatformReport,
    ProbeResult,
    ProcessControlProvider,
    build_report,
    module_available,
)
from graph_works_core.workspace import anchors
from graph_works_core.workspace.layout import layout_for


def test_the_schema_version_is_frozen_at_one() -> None:
    """Scripts consume this output; the version is part of the contract."""
    assert SCHEMA_VERSION == 1


def test_fcntl_is_named_as_posix_only() -> None:
    """The one irreducible platform fact lives here and nowhere else."""
    assert "fcntl" in POSIX_ONLY_MODULES


def test_a_posix_only_module_is_unavailable_on_win32() -> None:
    """Asserted from a POSIX box: this is the Windows answer, not the host's."""
    assert module_available("fcntl", "win32") is False


def test_a_posix_only_module_is_available_on_the_running_posix_host() -> None:
    assert module_available("fcntl", sys.platform) is True


def test_an_unknown_module_is_unavailable_on_every_platform() -> None:
    """A typo must not read as 'present'."""
    assert module_available("no_such_module_xyz", "win32") is False
    assert module_available("no_such_module_xyz", sys.platform) is False


def test_a_portable_module_is_available_on_win32() -> None:
    """`module_available` is not 'is this platform POSIX?' — a stdlib module
    CPython ships everywhere answers True for a foreign platform too."""
    assert module_available("json", "win32") is True


def test_unavailable_derives_the_posix_only_component_list() -> None:
    """The item's fourth required field is derived, not maintained."""
    report = PlatformReport(
        schema_version=SCHEMA_VERSION,
        platform="win32",
        python="3.12.0",
        capabilities=(
            Capability("a", "x", "available", "", (), "m"),
            Capability("b", "y", "unavailable", "", (), "m"),
            Capability("c", "z", "unknown", "", (), "m"),
        ),
        probes=(),
    )

    assert report.unavailable == ("b",)


def test_a_probe_result_records_whether_it_agrees() -> None:
    result = ProbeResult("dispatch-backend", "unavailable", "orca not on PATH", agrees_with_declared=False)

    assert result.agrees_with_declared is False


def test_the_durability_tier_is_available_on_posix() -> None:
    capability = DurabilityTierProvider().declare(sys.platform)

    assert capability.name == "durability-tier"
    assert capability.status == "available"
    assert capability.value == "posix-strong"
    assert capability.guarantees  # a tier name with no contract surfaces nothing


def test_the_durability_tier_is_degraded_on_win32() -> None:
    """Both tiers run — the weak tier is `degraded`, not `unavailable`. A
    refusal is a declared contract statement (D-002), not an incident."""
    capability = DurabilityTierProvider().declare("win32")

    assert capability.status == "degraded"
    assert capability.value == anchors.WINDOWS_REVALIDATED_TIER


def test_the_durability_tier_names_the_module_that_answered() -> None:
    """Traceability is the whole reason `provider` exists."""
    capability = DurabilityTierProvider().declare("win32")

    assert capability.provider == "graph_works_core.workspace.anchors"


@pytest.mark.parametrize("platform_name", ["linux", "darwin", "win32"])
def test_the_durability_tier_is_asked_of_the_engine_not_proxied(platform_name: str) -> None:
    """The provider's own docstring promised this once a second anchor landed."""
    report = build_report(platform_name=platform_name)
    capability = next(c for c in report.capabilities if c.name == "durability-tier")
    assert capability.value == anchors.anchor_tier(platform_name)
    assert capability.status != "unavailable"  # never "unavailable" -- both tiers run
    assert capability.guarantees  # the tier's declared contract, not ()


def test_the_durability_tier_provider_no_longer_reads_fcntl() -> None:
    """`transactions` stopped importing fcntl at module scope, so the old
    proxy was measuring the wrong thing as well as the wrong way."""
    source_file = inspect.getsourcefile(DurabilityTierProvider)
    assert source_file is not None
    source = Path(source_file).read_text(encoding="utf-8")
    provider = source.split("class DurabilityTierProvider")[1].split("\nclass ")[0]
    assert "fcntl" not in provider


def test_the_durability_tier_is_not_probeable() -> None:
    """Running a real transaction is not read-only. The report says so and
    names the manual verification run as the evidence instead."""
    result = DurabilityTierProvider().probe(layout=None)  # type: ignore[arg-type]

    assert result is not None
    assert result.status == "unknown"
    assert "not probeable" in result.detail


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["orca", "--version"], returncode=returncode, stdout=stdout, stderr="")


def test_the_dispatch_backend_is_unresolved_on_every_platform() -> None:
    """No resolver exists yet. The provider says so; it does not anticipate one."""
    for platform_name in ("darwin", "linux", "win32"):
        capability = DispatchBackendProvider().declare(platform_name)

        assert capability.value == "unresolved"
        assert capability.status == "unknown"
        assert "resolver" in capability.detail


def test_a_present_orca_probes_available_and_disagrees_with_unresolved() -> None:
    """`unresolved` is not `unavailable`: finding a working orca is news, and
    the disagreement is the point of reporting both."""
    provider = DispatchBackendProvider(
        which=lambda _name: "/usr/local/bin/orca",
        run=lambda *_args, **_kwargs: _completed(0, "orca 1.2.3\n"),
    )

    result = provider.probe(layout=None)  # type: ignore[arg-type]

    assert result is not None
    assert result.status == "available"
    assert result.agrees_with_declared is False


def test_an_absent_orca_probes_unavailable_without_running_anything() -> None:
    calls: list[object] = []

    def _run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _completed(0)

    provider = DispatchBackendProvider(which=lambda _name: None, run=_run)
    result = provider.probe(layout=None)  # type: ignore[arg-type]

    assert result is not None
    assert result.status == "unavailable"
    assert "PATH" in result.detail
    assert calls == []


def test_a_nonzero_orca_probes_unavailable() -> None:
    provider = DispatchBackendProvider(
        which=lambda _name: "/usr/local/bin/orca",
        run=lambda *_args, **_kwargs: _completed(127),
    )

    result = provider.probe(layout=None)  # type: ignore[arg-type]

    assert result is not None
    assert result.status == "unavailable"


def test_a_timing_out_orca_probes_unavailable_rather_than_raising() -> None:
    """A diagnostic verb that crashes while diagnosing is worthless."""

    def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="orca", timeout=5.0)

    provider = DispatchBackendProvider(which=lambda _name: "/usr/local/bin/orca", run=_run)
    result = provider.probe(layout=None)  # type: ignore[arg-type]

    assert result is not None
    assert result.status == "unavailable"
    assert "timed out" in result.detail


def test_an_unlaunchable_orca_probes_unavailable_rather_than_raising() -> None:
    def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("Exec format error")

    provider = DispatchBackendProvider(which=lambda _name: "/usr/local/bin/orca", run=_run)
    result = provider.probe(layout=None)  # type: ignore[arg-type]

    assert result is not None
    assert result.status == "unavailable"


def test_the_file_lock_is_flock_on_posix() -> None:
    capability = FileLockProvider().declare(sys.platform)

    assert capability.status == "available"
    assert capability.value == "fcntl.flock"


def test_the_file_lock_is_available_on_win32_via_msvcrt_and_names_its_sites() -> None:
    capability = FileLockProvider().declare("win32")

    assert capability.status == "available"
    assert capability.value == "msvcrt.locking"
    assert "work_tracker_okf" in capability.detail
    assert "10s" in capability.detail


def test_every_named_lock_site_still_imports_the_portable_lock_helper() -> None:
    """The detail names three files. If one stops importing `okf_ext.locking`
    this report is stale and must be updated with it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    for site in LOCK_SITES:
        matches = list(root.glob(f"packages/*/src/{site}"))
        assert matches, site
        assert "okf_ext.locking" in matches[0].read_text(encoding="utf-8")


def test_the_file_lock_probe_takes_and_releases_a_real_lock(tmp_path) -> None:
    layout = layout_for(tmp_path / ".works")
    layout.cache_dir.mkdir(parents=True)

    result = FileLockProvider().probe(layout)

    assert result is not None
    assert result.status == "available"
    assert result.agrees_with_declared is True


def test_the_file_lock_probe_reports_rather_than_raises_when_the_cache_is_unwritable(tmp_path) -> None:
    layout = layout_for(tmp_path / ".works")
    unwritable = layout.cache_dir
    unwritable.parent.mkdir(parents=True, exist_ok=True)
    unwritable.parent.chmod(0o500)
    try:
        result = FileLockProvider().probe(layout)
    finally:
        unwritable.parent.chmod(0o700)

    assert result is not None
    assert result.status == "unavailable"
    assert result.agrees_with_declared is False


def test_process_control_is_available_on_posix() -> None:
    capability = ProcessControlProvider().declare(sys.platform)

    assert capability.status == "available"
    assert capability.value == "workflow-local"


def test_process_control_is_unavailable_on_win32() -> None:
    capability = ProcessControlProvider().declare("win32")

    assert capability.status == "unavailable"
    assert "SIGKILL" in capability.detail


def test_process_control_has_no_probe() -> None:
    """Sending a signal to prove signalling works is not read-only."""
    assert ProcessControlProvider().probe(layout=None) is None  # type: ignore[arg-type]


def test_every_provider_is_registered_exactly_once() -> None:
    names = [provider.name for provider in PROVIDERS]

    assert names == ["durability-tier", "dispatch-backend", "file-lock", "process-control"]
    assert len(set(names)) == len(names)


def test_the_default_report_describes_the_running_platform() -> None:
    report = build_report()

    assert report.platform == sys.platform
    assert report.schema_version == 1
    assert report.python.startswith("3.")
    assert report.probes == ()
    assert [c.name for c in report.capabilities] == [p.name for p in PROVIDERS]


def test_the_windows_report_is_asserted_from_a_posix_box() -> None:
    """Acceptance property 1. Not a testing convenience: with no Windows CI,
    this is the only place in the epic where a Windows-shaped answer is
    checked on the machine the work is done on."""
    report = build_report(platform_name="win32")

    assert report.platform == "win32"
    # durability-tier moved from "unavailable" to "degraded" once the tier
    # record made the weak tier a declared contract rather than an incident.
    assert set(report.unavailable) == {"process-control"}


def test_the_default_report_needs_no_workspace_and_runs_nothing(monkeypatch) -> None:
    """Acceptance property 3."""

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the default run must not start a subprocess")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    report = build_report(platform_name="win32")

    assert report.probes == ()


def test_probing_without_a_layout_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="workspace"):
        build_report(probe=True)


def test_probing_collects_one_result_per_probeable_capability(tmp_path) -> None:
    layout = layout_for(tmp_path / ".works")
    layout.cache_dir.mkdir(parents=True)

    report = build_report(layout=layout, probe=True)

    probed = {result.capability for result in report.probes}
    assert probed == {"durability-tier", "dispatch-backend", "file-lock"}
    assert "process-control" not in probed


def test_a_probe_never_replaces_the_declared_value(tmp_path) -> None:
    """Acceptance property 4, at the data layer: both survive into the report."""
    layout = layout_for(tmp_path / ".works")
    layout.cache_dir.mkdir(parents=True)

    report = build_report(layout=layout, probe=True)
    declared = {c.name: c.value for c in report.capabilities}

    assert declared["dispatch-backend"] == "unresolved"
    assert any(r.capability == "dispatch-backend" for r in report.probes)
