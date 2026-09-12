# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`config-io` is Band 1 of the graph-works layering: schema-driven, git-like
scoped configuration over YAML — precedence resolution, a validated write
path, and a JSON projection for non-Python consumers. It is a pure mechanism
package. It **never discovers a workspace, never reads `os.environ` on its own
initiative, and never knows a file or directory name.** The catalog, the
store, the environment mapping, and the projection target are always arguments
supplied by the caller. Reading `packages/config-io/README.md` first is worth
it — it documents the design rationale in more depth than this file repeats.

## Commands

This package runs under `uv run --package config-io`, not a bare `uv run`
(the root `testpaths` covers okf-io/okf-ext only). The root justfile's exact
invocations (`types` runs twice, once per `--platform` arm):

```bash
uv run --package config-io mypy --strict --platform linux packages/config-io/src
uv run --package config-io mypy --strict --platform win32 packages/config-io/src
uv run --package config-io pytest packages/config-io/tests
uv run --package config-io pytest packages/config-io/tests \
  --cov=config_io --cov-branch --cov-report=term-missing --cov-fail-under=95
```

Subset examples:

```bash
uv run --package config-io pytest packages/config-io/tests/test_registry_write.py
uv run --package config-io pytest -k "persistence or rollback"
```

Lint is repo-wide (`just lint` / `uv run ruff check . && uv run ruff format --check .`)
and covers this package too — no separate per-package invocation.

## Architecture

### Module layout

- `entries.py` — the `ConfigEntry` schema, `WritePolicy` vocabulary, `coerce()`.
  No key/file/variable literals.
- `dotted.py` — `get` / `has` / `set_in` / `unset_in` over nested mappings by
  dotted key. All reads are **total**: a key that runs past a scalar or
  through a missing branch is just absent, never an exception.
- `store.py` — the `ConfigStore` Protocol (structural, not inherited), the
  `LayeredStore` Protocol (`@runtime_checkable`, for two-layer stores), and the
  two shipped implementations `PlainYamlStore` and `LayeredYamlStore`. This is
  the only module that imports `yaml`; `LayeredYamlStore` composes two plain
  stores and parses nothing itself. `PlainYamlStore` reads and writes
  **YAML 1.2** through `ruamel.yaml` `typ="safe"` — so `yes`/`no`/`on`/`off`
  are strings, `012` is twelve, and `1:30` is a string. Writes disable the
  representer's key sorting, so a one-key `set` does not reorder the file.
- `registry.py` — resolution (`resolve_key`, `resolve_all`, `expand_wildcards`)
  and the write path (`set_key`, `unset_key`).
- `projection.py` — `write_projection()`, atomic JSON rendering of a store's
  explicit values. The one module in the package that imports `os` (for
  `os.fdopen` in the atomic tempfile write).
- `errors.py` — the refusal taxonomy and `StoreValidationError`.

### Precedence resolution

For a `kind="manifest"` entry: **env var counterpart > local explicit value >
base explicit value > entry default.** `origin == "local"` is reported only
when the store satisfies the `LayeredStore` Protocol *and* the upper layer
holds the key; `shadowed` then carries the lower layer's value. Every
non-layered store collapses the two explicit tiers into today's `"manifest"`.
`kind="env-only"` entries never consult the store at all. Reads are fail-open
— a malformed env value resolves to its raw string rather than raising;
resolution never fails on the way past, the consumer surfaces the problem.
`Resolved.shadowed` carries the stored value an env override is hiding, so a
`list`-style view can show what's masked.

`environ: Mapping[str, str]` is a **required keyword**, with no `os.environ`
fallback anywhere on the resolution path — `import os` is structurally absent
from `entries.py`, `registry.py`, `dotted.py`, `store.py`, `errors.py`.
`tests/test_boundaries.py` walks the AST of every module under `src/config_io`
and asserts this mechanically (`RESOLUTION_PATH` in that file); a new module
added to the package must be explicitly filed into either `RESOLUTION_PATH` or
`EXEMPT` in that test, not silently picked up. The same test also asserts
`config_io` imports no sibling workspace package, deriving the sibling set
from the filesystem rather than a hardcoded list.

### The validated write path (`set_key`)

This is the package's actual differentiator over dynaconf/pydantic-settings —
neither offers a write path. `set_key` in `registry.py` does, in order:

1. `_writable_entry` — looks up the catalog entry and checks refusals in a
   fixed order: **unknown-key, then write-policy, then secret, then
   env-only.** Unknown must come first because policy now lives on the
   catalog entry itself — there's no hardcoded name list to check first.
2. `coerce()` the raw string value against the entry's declared type.
3. Read the store, apply the mutation via `dotted.set_in`, snapshot, write.
4. **Validate**: re-read via `store.read()`; a `StoreValidationError` here
   means the coerced value was individually well-formed but broke a store
   invariant — roll back to the snapshot and re-raise as `InvalidValueError`.
5. **Persistence check**: re-read via `store.read_explicit()` and confirm the
   key actually landed. If not, roll back and raise `RegistryError`. This
   guards against a lossy store (e.g. one with a fixed allowlist of
   serialised blocks) silently dropping a key outside that allowlist. A key
   that reads back as absent *because its value equals the entry default* is
   not a drop — a store may legitimately omit that.
6. Regenerate the JSON projection **only if `projection=` was passed.**

`PlainYamlStore` is lossless (read == read_explicit, no defaults injected), so
steps 4-5 can never actually fire against it — they exist for callers
supplying their own lossy store.

`unset_key` is much thinner: it checks write-policy refusals via
`_writable_entry` but does **not** run the validate-or-persistence-check
sequence that `set_key` does.

### Refusal taxonomy — order matters, and it's a table, not branching logic

All seven refusals derive from `RegistryError(ValueError)`, except
`StoreValidationError`, which is deliberately **not** a `RegistryError` — it's
how a store reports its own content is invalid (travels the other direction,
store → caller), so `except RegistryError` never accidentally swallows it.

Refusal selection in `registry.py` is a `dict[write_policy, ErrorClass]`
(`_POLICY_REFUSALS`) plus a matching message table (`_POLICY_MESSAGES`) — no
key names appear in either. Any remediation text comes only from the catalog
entry's own `write_hint`, appended verbatim. **A key that must be refused
needs a declared catalog entry carrying that policy** — there is no fallback
name list. Forgetting to declare e.g. a tooling-stamped provenance key does
not refuse it; it just makes it look ordinarily writable.

`secret` and `kind="env-only"` stay separate dataclass fields rather than
folded into `write_policy`, specifically so a catalog can't declare
`secret=True` and `write_policy="writable"` at once and mean two
contradictory things.

### The store seam and the persistence invariant

`ConfigStore` is a `Protocol` — six methods, structural, never inherited.
`restore(None)` has a specific, easy-to-miss meaning: "there was nothing
there before the write; remove it" — not "do nothing." Getting this wrong in
a custom store makes rollback silently leave a stray file behind.

`fingerprint()` returns one `Fingerprint(mtime, sha256)` object rather than
two separate accessor methods, specifically so the projection's `_meta` can
never end up with one field real and the other `None`.

`LayeredYamlStore` refuses `write` / `snapshot` / `restore` with `TypeError`.
The merged view is the wrong thing to write back through, and making that an
immediate error is cheaper than debugging a committed manifest that silently
grew every machine-local value. Write by naming a layer.

### The JSON projection

`write_projection()` renders **explicit values only** — no defaults merged.
An absent key in the projection means "the consumer should apply its own
default," not "this key doesn't exist." Written atomically (mkstemp + chmod
0o644 + `Path.replace`) because consumers poll it concurrently and must never
observe a partial write.

`_meta.source_mtime` / `_meta.source_sha256` are named that way deliberately
— the seed called them `manifest_*`, which was caller vocabulary leaking into
a package that must not know its caller (the same class of leak as a
hardcoded key name). Anyone porting an old consumer that reads `manifest_*`
needs to update it.

`_meta` grows two more keys — `overlay_mtime` / `overlay_sha256` — when and
only when the store satisfies `LayeredStore`. "Overlay" is store vocabulary; a
caller that calls its upper layer something else (graph-works calls it
`workspace.local.yaml`) still reads these key names.

Caller gotcha: `set_key` / `unset_key` only regenerate the projection when
`projection=` is explicitly passed. There's no way to statically enforce
"write ⇒ regenerate" — a caller that forgets leaves a stale projection with no
signal to a consumer reading it.

### Wildcard catalog entries

A `ConfigEntry.key` may contain exactly one `*` segment (e.g.
`roles.*.description` or `plugin.backend_overrides.*`). `find_entry` always
tries an exact match first, so a concrete entry beats a wildcard entry
regardless of catalog ordering. `expand_wildcards` (in `registry.py`) is what
turns a wildcard entry into the list of concrete stored keys it currently
matches — it inspects `store.read_explicit()`, not the catalog, so it only
ever returns keys that actually exist. `resolve_all` unions non-wildcard
catalog keys with this expansion; a wildcard entry with nothing stored under
it contributes no `Resolved` at all.

## Gotchas that span multiple files

- Coercion failure (`InvalidValueError` from `coerce()`) and store-invalidity
  failure (`InvalidValueError` raised by `set_key` after re-reading) are the
  *same exception class* raised from two different call sites in
  `registry.py` for two different reasons. Don't assume every
  `InvalidValueError` means the raw string didn't parse.
- `resolve_key`'s fail-open env coercion (`try: coerce(...) except
  RegistryError: value = raw_env`) means a badly-typed env var is silently
  passed through untyped in `Resolved.value` — check `Resolved.origin` if you
  need to know whether coercion actually succeeded.
- The `dotted` module's `unset_in` prunes empty parent mappings bottom-up
  after removal, so a store's own "omit key when empty" logic sees a clean
  absence rather than an empty dict husk. If you reimplement dotted-path logic
  elsewhere in the workspace, this pruning step is easy to miss.
- `tests/test_boundaries.py` is not just a lint check — adding a new module to
  `src/config_io` without adding it to `RESOLUTION_PATH` or `EXEMPT` in that
  test file will fail `test_the_resolution_path_list_is_complete` (or the
  equivalent completeness assertion), by design.
