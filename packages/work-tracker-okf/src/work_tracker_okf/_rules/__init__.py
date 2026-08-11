"""The lane rule catalog: 25 codes, four topic modules, twelve rule functions.

**The module name is the code prefix**, asserted mechanically in
`test_lane_catalog.py` exactly as okf-io's `test_catalog.py` asserts its own
eight.

The four prefixes had to clear thirteen taken names -- okf-io's eight
(`computation`, `frontmatter`, `legacy`, `lifecycle`, `links`, `provenance`,
`reserved`, `trust`) and okf-ext's five (`schemas`, `sections`, `health`,
`render`, `tags`). Only the first eight make `validate()` raise; colliding with
an okf-ext prefix is legal and still wrong, because the conformant vault runs
`schema_rule` and `section_rule` in the same report. **`lifecycle` is among the
taken eight**, so the module that was literally called `lifecycle_lint` could
not keep its name -- which is the push that made the split worth doing rather
than a rename worth arguing about.

Topics group by **what the rule reads**, which is okf-io's own organizing
principle: `lifecycle.py` there is the rules for `status` and `stale_after`,
`provenance.py` the rules for `sources`.

Every topic exports `rules(config)`, including the two that inject nothing
(C5-E). A heterogeneous registry -- some tuples, some callables -- would make the
catalog-completeness test special-case half its own subjects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from okf_io import Rule

from work_tracker_okf._rules import graph, plan, state, targets
from work_tracker_okf._rules._common import LaneConfig

RULES_BY_TOPIC: Mapping[str, Callable[[LaneConfig], tuple[Rule, ...]]] = MappingProxyType(
    {
        "graph": graph.rules,
        "plan": plan.rules,
        "state": state.rules,
        "targets": targets.rules,
    }
)

CODES_BY_TOPIC: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "graph": graph.CODES,
        "plan": plan.CODES,
        "state": state.CODES,
        "targets": targets.CODES,
    }
)

TOPICS: frozenset[str] = frozenset(RULES_BY_TOPIC)

CATALOG: frozenset[str] = frozenset(code for codes in CODES_BY_TOPIC.values() for code in codes)
