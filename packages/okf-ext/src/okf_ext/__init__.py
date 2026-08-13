"""okf-ext — beyond-spec capabilities over any OKF v0.2 bundle.

Tier 2 of a three-tier workspace (spec §2). Tier 1 is `okf-io`, the spec and
nothing else. Tier 3 is applications that produce or consume bundles. The
dependency direction is one-way: nothing here can affect the core's purity.

One distribution, capabilities as self-contained subpackages:

    from okf_ext import tags
    plan = tags.plan_rename(bundle, "kpi", "metric")

**This module imports no capability.** Installing okf-ext for one capability
must not load the machinery of the others, and `from okf_ext import tags`
imports the submodule without any help from here. That is also what keeps a
future graduation to a standalone distribution a directory move rather than a
rewrite.
"""

from __future__ import annotations

from okf_ext.context import DEFAULT_NORMALIZATION, ExtContext, NormalizationPolicy

__version__ = "0.4.5"

__all__ = [
    "DEFAULT_NORMALIZATION",
    "ExtContext",
    "NormalizationPolicy",
    "__version__",
]
