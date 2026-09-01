# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`packages/models-io` is Band 1 of the graph-works layering: guarded
constructors for AWS Bedrock and the Vercel AI Gateway — two chat models and
one embeddings client. The base distribution has **zero runtime
dependencies**; each provider stack lives behind a pyproject extra
(`bedrock` = `langchain-aws` + `boto3`, `vercel` = `langchain-openai` +
`openai`) and is imported lazily *inside the constructor function body*, not
at module level. `import models_io` therefore costs nothing beyond stdlib,
and installing one extra never drags in the other. `loader.py` imports
`BaseChatModel`/`Embeddings` only under `TYPE_CHECKING`, which is what makes
the zero-runtime-dependency claim hold for the return annotations too.

Band-1 also means: this package never reads the process environment, never
discovers a workspace, and never knows a file or directory name. There is no
carve-out — `tests/test_boundaries.py` walks every module's AST and asserts
`os` is not imported anywhere, and that no module imports a sibling workspace
package (the sibling set is derived from `packages/*/src`, not hardcoded, so
a new band-1 package is covered automatically).

## Commands

Because the provider stacks are optional extras, plain `uv run pytest` /
`uv run mypy` from the repo root will not see `langchain_aws`,
`langchain_openai`, etc. Both extras must be requested explicitly, and the
`--package` flag is what makes `uv` resolve this member's own extras rather
than the workspace root's. This is exactly what the root `justfile` does
(`types` twice, once per `--platform` arm):

```bash
uv run --package models-io --extra bedrock --extra vercel mypy --strict --platform linux packages/models-io/src
uv run --package models-io --extra bedrock --extra vercel mypy --strict --platform win32 packages/models-io/src
uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests
uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests \
    --cov=models_io --cov-branch --cov-report=term-missing --cov-fail-under=95
```

Subset examples (still need both extras for anything that touches
`bedrock.py`/`vercel.py`/`embeddings.py`):

```bash
uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests/test_normalize.py
uv run --package models-io --extra bedrock --extra vercel pytest -k "access_denied or gateway"
```

`test_extras.py` and `test_boundaries.py` are the exception — they're pure
stdlib/mechanics tests (AST walks, `sys.modules` shimming, a subprocess
check) and would still pass without the extras installed, but there's no
reason to run them in isolation.

Note `pyproject.toml` sets `asyncio_mode = "auto"` locally (three
`async def test_*` live in `test_bedrock.py`/`test_vercel.py`). The workspace
root does not set this anywhere else, so this package's own `pyproject.toml`
is what makes async tests here work — don't assume it's inherited.

## Architecture

### The guarded-subclass pattern

Both `make_bedrock_llm` and `make_gateway_llm` return a subclass
(`_GuardedChatBedrockConverse`, `_GuardedChatOpenAI`) rather than a decorated
or monkeypatched instance. This is forced, not stylistic: both underlying
classes are Pydantic v2 `BaseModel`s with `extra='forbid'` and `__slots__`,
so `llm.invoke = fn` raises `ValueError`. The subclasses override
`invoke`/`ainvoke`, delegating to `_original_invoke`/`_original_ainvoke`
(thin wrappers around the parent's method) — that indirection exists purely
as a monkeypatch seam so unit tests can stub the network call without a real
API. The leading underscore on all of this is deliberate: it's a test seam,
not public API.

Per-instance error context (`_model_id_for_errors` on Bedrock,
`_base_url_for_errors`/`_credential_hint_for_errors` on the gateway) is set
via `object.__setattr__` in `build()`, bypassing Pydantic's `extra='forbid'`
validator — normal attribute assignment would raise on these models.

`make_bedrock_embeddings` has no guarded subclass at all: `BedrockEmbeddings`
returns `list[float]`, not a message with content shape, so there's nothing
for the access-denied translation or normalization to attach to (see
`embeddings.py`'s module docstring). Don't add one without a reason.

### Access-denied taxonomy

One base class, `ModelsIoError`, so a caller can write a single `except`
clause:

```
ModelsIoError
├── BedrockAccessDenied     Bedrock's AccessDeniedException on InvokeModel
├── GatewayAccessDenied     falsy api_key at construction, OR openai.AuthenticationError (401) at invoke
└── ProviderNotInstalled    the extra backing the constructor is absent
```

Message-formatting functions (`_format_access_denied_message`,
`_format_gateway_access_denied_message`, `_format_missing_extra_message`)
live in `errors.py`, not beside the guards that raise them. This is
deliberate: `make_gateway_llm` refuses a falsy `api_key` *before* it knows
whether the `vercel` extra is even installed, so the gateway message
formatter has to be reachable without importing `models_io.vercel`.

`ProviderNotInstalled` always preserves the original `ImportError` as
`__cause__` — check that when writing tests against it, not just the message
string.

### Caller-supplied credentials

- `make_gateway_llm` requires `api_key` as a keyword-only argument. A falsy
  value is refused immediately at construction time with
  `GatewayAccessDenied` (the "preflight" half of that exception's two
  documented cases) — before the `vercel` extra is even imported.
- `credential_hint` is a cosmetic-only parameter: it does not change auth
  behavior, only the refusal message. Historically this package read
  `AI_GATEWAY_API_KEY` itself and named it in error text; now that it reads
  nothing from the environment, the caller has to hand the variable name
  back in for the message to reference it. Omit it and the message names
  only the gateway base URL.
- `make_bedrock_llm` / `make_bedrock_embeddings` take **no credentials
  argument at all** — `boto3` resolves AWS credentials on its own, below
  this package. Do not add a credentials kwarg to the Bedrock constructors;
  that would violate the band-1 boundary this package holds mechanically.

### Content normalization

`normalize.py`'s `normalize_content()` is shared by both guarded chat
subclasses (imported by each, never duplicated) and is the reason neither
provider module needs to know about the other. The problem: Bedrock's
Converse API returns `response.content` as a **list** of content blocks
(text + reasoning) for "thinking" models (e.g. gpt-oss-120b, minimax-m2.5)
rather than a plain string, which breaks any downstream consumer that calls
`.strip()` or regexes over `.content`.

The trigger is content **shape** (`isinstance(content, list)`), never a
model-name check — do not special-case this by model id if you touch it.
Behavior: non-list content passes through unchanged; for list content, bare
`str` items and `{"type": "text", ...}` dict blocks are joined into the new
`response.content` string, and every other block (reasoning, etc.) is
preserved — never dropped — on `response.additional_kwargs["reasoning"]`.
This "reasoning is preserved" behavior is called out as LOCKED in the
docstring; don't change it without updating that decision explicitly.

It's public at module level (importable) but intentionally excluded from
`models_io.__all__` — the chartered concept for external callers is the
guarded subclasses/constructors, not this helper directly.

### Pricing (a separate concern living in the same package)

`pricing.py` is unrelated to the guarded-constructor/normalization story —
it's a hardcoded USD-per-million-token table (`PRICES`, ~25 Bedrock/Anthropic
model IDs) plus `cost_for_usage(model, usage)`. It lives here because it's
model-domain data with more than one consumer (`subagents-io` prices trace
records with it; an eval harness prices sweeps with it) and neither should
import the other. `subagents-io` receives it as an **injected callable**
(`cost_for_usage` is structurally assignable to that package's
`PriceLookup` protocol because `usage` is typed `Mapping[str, int]`, not
`dict` — callable parameters are contravariant) rather than importing
`models_io`.

`UnknownModelError` subclasses `KeyError` on purpose, so a caller already
guarding `except KeyError` needs no change. `PRICES` is reachable at
`models_io.pricing.PRICES` but is not part of the public surface — don't
promote it to `__all__` without cause. Prices are updated by hand, dated in
the module docstring; there's no automated freshness check.

## Invariants enforced mechanically (read before changing structure)

- `tests/test_boundaries.py`: every module is enumerated in `ALL_MODULES` —
  adding a new module without updating that tuple fails the test
  deliberately, so a new file is a decision, not a silent addition. Only
  `bedrock.py`, `embeddings.py`, `vercel.py` (`PROVIDER_MODULES`) may import
  from `{boto3, botocore, langchain_aws, langchain_openai, openai}`; every
  other module must import none of them. `os` is banned everywhere, no
  exceptions.
- `tests/test_extras.py`: proves the extras actually isolate. It fakes an
  absent extra in-process via `sys.modules[name] = None` (which makes the
  import raise), and separately proves in a **subprocess** that
  `import models_io` alone loads neither provider stack — a subprocess is
  required there because by that point in the suite `test_bedrock.py`/
  `test_vercel.py` have already imported both stacks into the interpreter.
- The root `[tool.importlinter]` also carries an `independence` contract over
  band-1 packages; it's a static, enumerated check, complementary to (not a
  replacement for) the AST walk above, which is derived from the filesystem
  and self-updates as packages are added.

If you add a fourth provider-touching module or a new cross-package import,
expect both `test_boundaries.py` and `lint-imports` (`just contracts`) to
need updates — check both, they check different directions of the same rule.
