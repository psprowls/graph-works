"""What counts as an ADR: the `Adr` type in the `Adr` schema's own directory.

ADR-chain linting is genuinely about ADRs, so this module names the type.
The directory is never a constant: it is the `Adr` schema's `x-okf-directory`,
and a workspace that declares no `Adr` schema has no ADRs to chain.
"""

from __future__ import annotations

from okf_ext.schemas import SchemaSet, declared_directories

_ADR = "Adr"


def adr_directory(schema_set: SchemaSet) -> str | None:
    """The `Adr` schema's `x-okf-directory`, or None when the set declares none."""
    return declared_directories(schema_set).get(_ADR)


def is_adr(concept_id: str, type_name: str, *, directory: str | None) -> bool:
    """An ADR is an `Adr` **in** *directory*. Both halves are required: an `Adr`
    elsewhere is misplaced, and any other type in `adrs/` is not one either."""
    return directory is not None and concept_id.startswith(directory) and type_name.strip() == _ADR


__all__ = ["adr_directory", "is_adr"]
