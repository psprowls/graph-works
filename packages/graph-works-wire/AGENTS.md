# AGENTS.md — graph-works-wire

The interface band's shared projection layer. Band: interface (above
`graph-works-core`, beside `graph-works-cli` and
`graph-works-serve`). Depends on `graph-works-core`, `config-io`,
`doc-wiki-okf` and `okf-ext` for their result types only.

## Modules

| Module | Holds |
|---|---|
| `work` | `gw work` projections (`next_payload`, `advance_payload`, `item_payload`, …) plus `next_blockers` and the private fragments they share. |
| `wiki` | bootstrap, scan, ingest, query, lint, drift, stats, proposal, tag and `page_payload` projections. |
| `config` | `resolved_payload`, `resolved_list_payload`, `hooks_payload`, `projection_payload`. |
| `errors` | `REASONS` and `error_envelope()` — the refusal envelope both interfaces emit. |
| `util` | `log_payload`, `log_read_payload`, `platform_payload`, `tokens_payload`, `line_endings_payload`. |
| `events` | Identity-only change-event and sequenced batch projections for SSE. |
| `_jsonable` | `jsonable()`, the `default=str` rule applied before encoding. |

## The contract

Every public `*_payload` returns only `dict` (str keys), `list`, `str`,
`int`, `float`, `bool` and `None`: `json.dumps(p)` needs no `default=` and
round-trips equal except for IEEE NaN's inherent `NaN != NaN` behavior. NaN is
preserved to keep existing encoded bytes compatible; contract tests compare it
with a NaN-aware equality rule. `tests/test_contract.py` enforces the contract
over an enumerated list of every projection.

`jsonable()` gives primitive mapping keys the same string spellings as the
standard encoder. Distinct input keys that collide after conversion cannot be
preserved by a plain `dict`; the helper refuses those mappings rather than
silently losing an entry.

## Adding a projection

1. Write it in the module for its command group, key by key — never
   `dataclasses.asdict` (a core field added would silently widen every
   interface's output).
2. Add `"<module>.<name>"` to `EXPECTED` in `tests/test_contract.py`.
3. Add at least one sample to `tests/samples_<module>.py`, covering each
   `None`/non-`None` branch.

## Gotchas

- **No encoding here.** No `json` import (the boundary test forbids it). The
  CLI's indent and any canonical encoding (C7) belong to the interface.
- **`config` mirrors `asdict` by hand.** Its key order is `asdict`'s field
  order; `tests/test_config.py` pins the field lists of `Resolved`,
  `ConfigEntry` and `HooksResult`, so a field added upstream fails there first.
- **No re-export in the CLI.** Callers import from here directly.
- Changing any projection's output changes `gw --json` bytes; the CLI's
  `tests/fixtures/json/*.golden.json` will catch it.
