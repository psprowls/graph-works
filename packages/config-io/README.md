# config-io

Schema-driven, git-like scoped configuration over YAML: precedence resolution,
a validated write path, and a JSON projection for consumers that cannot run
Python.

Band 1 of the graph-works layering. This package **never discovers a
workspace, never reads the process environment on its own initiative, and
never knows a file or directory name.** The catalog, the store, the
environment mapping and the projection target are all arguments.

## Why this and not dynaconf or pydantic-settings

Both of those are excellent at the read half — layered sources, typed
coercion, validation on load. Neither offers a **write** path at all.

`config-io`'s differentiator is `set` / `unset` / `list` against a declared
catalog: per-key validation *before* any I/O, a closed refusal taxonomy so a
caller can react to *why* a write was refused rather than parse a message,
rollback if the written value leaves the store invalid, and a persistence
check so a lossy store can never silently discard a user's write. That is the
`validated-scoped-write-path` primitive, and it is the reason this package
exists rather than a dependency.

## Usage

```python
from pathlib import Path

from config_io import ConfigEntry, PlainYamlStore, resolve_all, resolve_key, set_key

CATALOG = (
    ConfigEntry(key="topic", type="str", default=None, description="Display name."),
    ConfigEntry(
        key="workflow.commit_strategy",
        type="str",
        default="per-task",
        allowed=("per-task", "at-end"),
        description="Commit cadence.",
    ),
)

store = PlainYamlStore(Path("config.yaml"))
projection = Path(".derived/config.json")

set_key(CATALOG, "topic", "My Wiki", store=store, projection=projection)
resolve_key(CATALOG, "topic", store=store, environ={}).value  # "My Wiki"
resolve_all(CATALOG, store=store, environ={})  # one Resolved per key
```

### Precedence

**env var counterpart > local explicit value > base explicit value > entry
default.** Only `kind="manifest"` entries consult the store; `kind="env-only"`
entries never touch it. The two explicit tiers are one tier for any store that
is not a `LayeredStore` — which is every store unless the caller supplies a
layered one.

Reads are fail-open. A malformed env value resolves to its raw string rather
than raising — the consumer surfaces the problem, resolution does not fail on
the way past. `Resolved.shadowed` carries what a higher tier is masking: the
merged stored value behind an env override, or the base layer's value behind a
local one.

## The store seam

`ConfigStore` is a `Protocol`. Implement it structurally; nothing is
inherited.

| Method | Contract |
|---|---|
| `read()` | Normalised, validated content, defaults injected. Raises `StoreValidationError` when the stored content is invalid. |
| `read_explicit()` | Raw stored values, nothing injected. Origin reporting needs it to tell "explicitly set" from "default"; the persistence check needs it to see what actually landed. |
| `write(data)` | Persist `data`, by whatever serialisation the store owns. |
| `snapshot()` | Opaque rollback token, or `None` when nothing is stored. |
| `restore(snapshot)` | Put a snapshot back. **`restore(None)` means "there was nothing there; remove it"** — not "do nothing". |
| `fingerprint()` | `Fingerprint(mtime, sha256)` for the projection's `_meta`, or `None` when nothing is stored. Returning one object is what keeps the two fields from disagreeing. |

`PlainYamlStore` ships with the package and is the only reason `ruamel.yaml` is
a dependency. It is lossless — `read` and `read_explicit` return the same
content and no defaults are injected — so the persistence check below can
never fire against it. A caller supplying its own store never loads a YAML
library at all.

It reads and writes **YAML 1.2**: `yes`/`no`/`on`/`off` are strings, a leading
zero is not an octal, and `1:30` is not a sexagesimal. Writes preserve
insertion order and emit non-ASCII literally.

`LayeredYamlStore(base=..., overlay=...)` is the second shipped
implementation: a **read-only** merged view over two `PlainYamlStore`s, the
overlay winning. Mappings deep-merge; lists, scalars and an explicit `null`
replace wholesale. `fingerprint()` reports the *base*; `overlay_fingerprint()`
reports the overlay.

`write` / `snapshot` / `restore` raise `TypeError`. That is not an oversight:
`set_key` is read-mutate-write over `store.read()`, so pointing it at a merged
mapping would copy every overlay value into the base file. A caller writes by
naming a layer — `set_key(catalog, key, value, store=layered.overlay)` — and
then regenerates the projection from the layered store itself.

It satisfies `LayeredStore`, a second structural Protocol
(`read_base_explicit`, `read_overlay_explicit`, `overlay_fingerprint`). The
read side uses `isinstance` against it and behaves exactly as before for any
store that does not satisfy it.

### The persistence invariant

`set_key` re-reads the store after writing and confirms the key actually
landed, restoring the previous state and raising if it did not. **Any lossy
store must not silently drop a written key.** A store with a fixed allowlist
of serialised blocks is the shape this exists for: a catalog key outside the
allowlist would otherwise vanish with no error and the user would be told the
write succeeded.

A key absent *because* its value equals the entry's declared default is not a
drop — a store may legitimately omit that, and it is equivalent to unset.

## The environment carve-out

`environ: Mapping[str, str]` is a **required** keyword on `resolve_key` and
`resolve_all`. There is no `os.environ` fallback, and `import os` is absent
from every module on the resolution path — `tests/test_boundaries.py` asserts
it.

The variable *names* come from `ConfigEntry.env_var`, which the caller
declares. So: **`config-io` reads only the variables the caller named, from a
mapping the caller handed it, and knows no variable name of its own.** Pass
`os.environ` once, at your own boundary.

(`projection.py` does import `os`, for `os.fdopen` in the atomic write. The
boundary test exempts it by name: writing a file atomically is not reading
the environment.)

## Refusals

`RegistryError(ValueError)` is the base for all seven refusals.

| Error | Fired by |
|---|---|
| `UnknownKeyError` | No catalog entry matches, exact or wildcard. Carries a `difflib` near-miss suggestion. |
| `LinkFileKeyError` | `write_policy="link-file"` |
| `ProvenanceKeyError` | `write_policy="provenance"` |
| `ReadOnlyKeyError` | `write_policy="read-only"` |
| `SecretKeyError` | `entry.secret` |
| `EnvOnlyKeyError` | `entry.kind == "env-only"` |
| `InvalidValueError` | Coercion failed, or the coerced value left the store invalid |

Checked in that order: unknown-key, then policy, then secret, then env-only.

`StoreValidationError` is deliberately **not** a `RegistryError`. It is not a
refusal — it is how a store reports that its own content is invalid — so
`except RegistryError` never swallows it.

Refusal selection is a table from `write_policy` to error class with no key
names in it, and the messages name no files. Remediation wording is the
caller's: put it in `ConfigEntry.write_hint` and it is appended verbatim.

### Obligation on the catalog

A key that must be refused needs a **declared catalog entry carrying the
policy**. There is no hardcoded name list to fall back on. In practice that
means declaring entries for the tooling-stamped provenance keys and the
hand-edited link-file keys that a caller previously refused by name. They then
appear in a `list` view — a fix, not a regression: they are real knobs that
were invisible.

## The projection

```python
write_projection(store, target)  # -> target
```

Explicit values only, no defaults merged — a consumer applies its own
`${VAR:-default}` fallbacks, so an absent key means "default". Written
atomically (tempfile plus replace), because consumers read it concurrently and
must never observe a truncation.

`_meta` carries `source_mtime` and `source_sha256`, both real or both `None`,
never a mix.

A `LayeredStore` adds `overlay_mtime` and `overlay_sha256` beside them, again
both real or both `None` — `None` meaning the upper layer's file is absent.
`source_*` keeps meaning "the lower, committed layer". The shape is additive
on purpose: a consumer written against the two-key `_meta` keeps working and
simply never checks the overlay.

> **Migration obligation.** The seed called these `manifest_mtime` /
> `manifest_sha256`. "Manifest" is caller vocabulary in a package that must
> not know its caller — the same class of leak as a hardcoded key name — so
> they were renamed. **A consumer reading the old keys to detect hand-edits
> must be updated.**

> **Caveat (`projection=` is optional).** `set_key` and `unset_key` regenerate
> the projection only when you pass `projection=`. Write⇒regenerate is
> therefore documentation, not a type: a caller that forgets leaves the
> projection stale, and a consumer reading it has no way to notice. This is
> the accepted cost of keeping these as plain functions rather than bundling
> catalog, store, environ and projection into a session object.

## Naming: the PyPI collision, deferred

**`config-io` is taken on PyPI.** Checked 2026-08-11: HTTP 200, v0.4.0,
*"Advanced config reading/writing/parsing for yaml/json configs"*. It is not
a squat — it is an active project in the same problem space, so its import
name is almost certainly `config_io` as well.

**Decision: ship as `config-io` / `config_io` and defer the publish name.**
Nothing is published, and the uv workspace resolves members by path, so the
collision costs nothing today.

- **Trigger:** the first attempt to publish this package to PyPI.
- **Leading candidates**, verified free on 2026-08-11: `scoped-config-io`
  (names the differentiator, keeps the `-io` suffix) and `config-registry-io`
  (names the seed module and the concept). Also free: `config-scope-io`,
  `graph-config-io`.
- **Stated risk:** the collision is on the *import* name too, so a rename
  later touches every call site rather than one line of `pyproject.toml`.
  Deferring is cheapest now and most expensive later. That is why the trigger
  and the candidates are recorded here rather than left open.

## Boundaries

The root `[tool.importlinter]` carries an `independence` contract over the five
band-1 packages, which forbids `config_io` importing any of them and any of them
importing `config_io`. `tests/test_boundaries.py` stays alongside it, because
the contract's `modules` list is enumerated while this walk derives its sibling
set from the filesystem. It asserts the two invariants that actually matter:

1. `config_io` imports no other workspace package — stronger than a layers
   contract, which only orders what it was told to enumerate.
2. Nothing on the resolution path imports `os`.

Plus: a YAML library (`ruamel`, and the `yaml` name it replaced) is imported by
`store.py` and by nothing else.
