LOG_FORMAT = """\
## Log format

One `## <ISO date>` section per day, newest first; one list item per entry
(OKF v0.2 §9). `okf_io.log` reports a non-ISO heading as
`reserved.log-heading-not-date`.

```
## YYYY-MM-DD

- **<Op>** <title> — <optional detail: which pages touched, what changed>
```

Valid ops: `scan`, `ingest`, `query`, `lint`, `create`, `update`, `delete`, `note`.

Grep the log: `grep -E "^## [0-9]{4}-" log.md | tail -10`\
"""


__all__ = ["LOG_FORMAT"]
