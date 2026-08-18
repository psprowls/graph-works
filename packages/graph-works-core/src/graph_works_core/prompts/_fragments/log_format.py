LOG_FORMAT = """\
## Log format

```
## [YYYY-MM-DD] <op> | <title>
<optional detail — which pages touched, what changed>
```

Valid ops: `scan`, `ingest`, `query`, `lint`, `create`, `update`, `delete`, `note`.

Grep the log: `grep "^## \\[" log.md | tail -10`\
"""


__all__ = ["LOG_FORMAT"]
