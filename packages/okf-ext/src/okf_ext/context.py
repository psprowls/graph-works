"""Cross-cutting configuration for every okf-ext capability.

This is the **shared layer**: capabilities import it, it imports no capability.
An `import-linter` layers contract enforces that direction, which is why
`NormalizationPolicy` lives here rather than in `okf_ext.tags.model` — an
`ExtContext` field typed with a `tags` type would invert the dependency and
break the contract on day one.

Every field defaults, so `ctx=None` always works and no caller is forced to
construct one.

**No `today` field.** Nothing in tags needs a clock, and `okf_io.validate()`
takes `today` from its own caller precisely so no hidden `date.today()` can
survive review. Carrying an unused field would invite someone to wire one in.

**No `ignore` field.** `ignore=` belongs to `okf_io.load_bundle()`, upstream
of anything okf-ext sees. `okf_ext.tags.DEFAULT_IGNORE` is a constant callers
splice into their own `load_bundle()` call instead, so no okf-ext function
ever changes behaviour because a file appeared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class NormalizationPolicy:
    """How a tag's canonical form is computed.

    The defaults describe the corpus rather than imposing on it: OKF tags in
    the wild are already lowercase-hyphen (`headline-metric`, `e-commerce`).
    """

    case: Literal["lower", "preserve"] = "lower"
    separator: str = "-"
    unicode_form: Literal["NFC", "NFKC", "none"] = "NFC"
    strip: bool = True


#: The policy used whenever a caller supplies no context.
DEFAULT_NORMALIZATION = NormalizationPolicy()


@dataclass(frozen=True, slots=True)
class ExtContext:
    """Configuration that rides alongside a bundle instead of wrapping it.

    A facade object (`ExtBundle(bundle).tags.rename(...)`) was rejected: it
    breaks the core's free-functions-over-frozen-data idiom, and a facade
    reaching across capabilities would silently couple them.
    """

    normalization: NormalizationPolicy = DEFAULT_NORMALIZATION
