"""`gw util platform`'s report: the types, the four providers, and the probes.

The property that matters most here is that a Windows report is asserted from
a POSIX box. The epic bought manual Windows verification and no CI, so
`build_report(platform_name="win32")` is the only place in this epic where
Windows-shaped behaviour is checked on the machine the work is done on.
"""

from __future__ import annotations

import sys

from graph_works_core.util.platform import (
    POSIX_ONLY_MODULES,
    SCHEMA_VERSION,
    Capability,
    DurabilityTierProvider,
    PlatformReport,
    ProbeResult,
    module_available,
)


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


def test_the_durability_tier_is_unavailable_on_win32() -> None:
    """Today the engine imports `fcntl` at module scope, so on native Windows
    it cannot be imported at all — the honest answer, not 'the weaker tier'."""
    capability = DurabilityTierProvider().declare("win32")

    assert capability.status == "unavailable"
    assert "fcntl" in capability.detail


def test_the_durability_tier_names_the_module_that_answered() -> None:
    """Traceability is the whole reason `provider` exists."""
    capability = DurabilityTierProvider().declare("win32")

    assert capability.provider == "graph_works_core.workspace.transactions"


def test_the_durability_tier_is_not_probeable() -> None:
    """Running a real transaction is not read-only. The report says so and
    names the manual verification run as the evidence instead."""
    result = DurabilityTierProvider().probe(layout=None)  # type: ignore[arg-type]

    assert result is not None
    assert result.status == "unknown"
    assert "not probeable" in result.detail
