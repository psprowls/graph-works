# gw-wiki

The wiki application slice over resolved `gw-kernel` workspace primitives.
It owns scan, ingest, query, lint/drift, proposals, wiki page and code reads,
repository operations, agent configuration, role support and prompt fragments.

Import APIs from their defining modules, for example:

```python
from gw_wiki.wiki_page.commands import run_page_read
from gw_wiki.agent_substrate.roles import packaged_roles
```

The packaged role catalog lives in `gw_wiki/models.toml`. Repository generations
retain the `gw-kernel` distribution version. The `vercel` extra enables the
optional model gateway backend. Cross-work tests use the development-only
`gw-work` dependency.

```bash
uv run --package gw-wiki --group dev pytest packages/gw-wiki/tests
```

See [AGENTS.md](AGENTS.md) for module guidance and test helper ownership.
