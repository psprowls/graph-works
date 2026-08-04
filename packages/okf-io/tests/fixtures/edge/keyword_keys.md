---
type: Metric
title: Keys that collide with Python keywords
not:
  - term: revenue minus product cost only
    why: that is the pre-FY2026 definition
    instead: revenue minus full COGS
from: legacy-import
class: finance
---

# Definition

`not:`, `from:`, and `class:` must survive in `fm.extra`. Attribute mapping
would break on all three — which is why `extra` mirrors rather than replaces.
