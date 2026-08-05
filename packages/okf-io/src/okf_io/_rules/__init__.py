"""The built-in rule catalog.

**The module name is the code prefix.** Every rule in ``_rules/trust.py`` emits
codes beginning ``trust.``, and ``test_catalog.py`` asserts it mechanically, so
the catalog cannot drift from its own file layout.

``okf_io._rules.links`` (the Links-topic rules) and ``okf_io.links`` (the graph)
are different things with the same last name. That is a deliberate consequence
of the prefix-equals-module invariant; the invariant is worth more than the
ambiguity costs, and only these two modules import both.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from okf_io._rules import (
    computation,
    frontmatter,
    legacy,
    lifecycle,
    links,
    provenance,
    reserved,
    trust,
)
from okf_io.validate import Rule

RULES_BY_TOPIC: Mapping[str, tuple[Rule, ...]] = MappingProxyType(
    {
        "computation": computation.RULES,
        "frontmatter": frontmatter.RULES,
        "legacy": legacy.RULES,
        "lifecycle": lifecycle.RULES,
        "links": links.RULES,
        "provenance": provenance.RULES,
        "reserved": reserved.RULES,
        "trust": trust.RULES,
    }
)

CODES_BY_TOPIC: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "computation": computation.CODES,
        "frontmatter": frontmatter.CODES,
        "legacy": legacy.CODES,
        "lifecycle": lifecycle.CODES,
        "links": links.CODES,
        "provenance": provenance.CODES,
        "reserved": reserved.CODES,
        "trust": trust.CODES,
    }
)

TOPICS: frozenset[str] = frozenset(RULES_BY_TOPIC)

RULES: tuple[Rule, ...] = tuple(rule for topic in sorted(RULES_BY_TOPIC) for rule in RULES_BY_TOPIC[topic])

CATALOG: frozenset[str] = frozenset(code for codes in CODES_BY_TOPIC.values() for code in codes)
