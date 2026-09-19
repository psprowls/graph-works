# graph-works-wire

Every typed graph-works result, projected to plain JSON data — one copy per
result family, shared by every interface (`gw … --json` and
`graph-works-serve`), so the interfaces cannot drift apart.

```python
from graph_works_wire import work

payload = work.status_payload(report)  # dict/list/str/int/float/bool/None only
```

Wire never encodes: each interface calls `json.dumps` itself (the CLI uses
`indent=2`). It never does I/O and never imports an interface framework.
Payloads round-trip through the standard encoder with equal values except for
IEEE NaN's inherent `NaN != NaN` behavior. NaN remains a float and keeps its
legacy encoded bytes; callers comparing decoded payloads must compare it with a
NaN-aware rule.

`jsonable()` converts every encoder-supported primitive mapping key to the
same string spelling the standard JSON encoder emits. A mapping whose distinct
keys convert to the same JSON object name cannot be represented losslessly as
a plain Python dictionary, so `jsonable()` refuses that collision instead of
silently discarding an entry.
