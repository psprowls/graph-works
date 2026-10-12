# gw-pipeline

Journaled work mutations, decision ownership, finish evidence, placement, gates,
guidance and transcript capture over resolved kernel workspace primitives.
The package root exports no APIs; import the defining modules.

Version 0.1.0, Python >=3.12. Kernel owns discovery, manifest/layout, bundle locks,
pending commits, anchors and commits. Only `transcript_capture` may delegate to
kernel discovery; all other pipeline services receive resolved paths. Source and
tests never import sibling slices or interfaces. Installed `testing` and
`test_support` modules are test-only; see [AGENTS.md](AGENTS.md) for the ownership
map and transaction-global C3 monkeypatch seam.

```bash
uv run --package gw-pipeline pytest packages/gw-pipeline/tests
just check-pkg gw-pipeline
```

## Platform

The engine delegates host filesystem primitives to kernel anchors. Both POSIX
and Windows durability tiers preserve the transaction-global monkeypatch seam.
