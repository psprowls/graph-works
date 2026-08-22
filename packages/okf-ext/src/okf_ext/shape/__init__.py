"""Declaration shapes shared by every capability that reads a `sections/`
directory.

    from okf_ext import shape
    declaration = shape.load_sections("kb/sections")

**The first shared module that reads files.** That is a widening of what
"shared" means and is stated rather than smuggled: the layer already held
`okf_ext.context`, which is configuration, and a declaration loader is
configuration that happens to live on disk.

`okf_ext.sections` re-exports every name here for one minor version -- the
README's own graduation recipe -- so `from okf_ext.sections import
load_sections` keeps working.

Imports stdlib and `ruamel.yaml`, and nothing else. It imports no capability
and no other shared module, and no other shared module imports it.
"""

from __future__ import annotations

from okf_ext.shape.loader import (
    DEFAULT_IGNORE,
    DEFAULT_SECTIONS_DIRNAME,
    SECTION_SUFFIXES,
    load_sections,
)
from okf_ext.shape.model import (
    FrontmatterOwnership,
    Ownership,
    SectionError,
    SectionSet,
    SectionSpec,
    TypeSections,
)

#: Ordered UPPER_SNAKE_CASE constants, then CapWords classes, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "DEFAULT_IGNORE",
    "DEFAULT_SECTIONS_DIRNAME",
    "SECTION_SUFFIXES",
    "FrontmatterOwnership",
    "Ownership",
    "SectionError",
    "SectionSet",
    "SectionSpec",
    "TypeSections",
    "load_sections",
]
