# models-io

Guarded model constructors for AWS Bedrock and the Vercel AI Gateway — two chat
models and one embeddings client: a closed access-denied taxonomy, list-content
normalization at the model boundary, and provider stacks isolated behind extras.

Band 1 of the graph-works layering. This package **never reads the process
environment, never discovers a workspace, and never knows a file or directory
name.** `packages/models-io/tests/test_boundaries.py` holds that mechanically
by walking this package's own AST — no module imports `os`, and no module
imports a sibling workspace package.

## Install

The base distribution has **zero runtime dependencies**. Each provider is an
extra, and installing one never drags in the other:

```bash
pip install 'models-io[bedrock]'   # langchain-aws, boto3
pip install 'models-io[vercel]'    # langchain-openai, openai
pip install 'models-io[bedrock,vercel]'
```

`make_bedrock_llm` and `make_gateway_llm` import their provider module lazily,
inside the function body. Calling a constructor whose extra is absent raises
`ProviderNotInstalled` — a typed, actionable error naming the extra to
install — rather than a bare `ImportError` from an import you did not write.

## Usage

```python
from models_io import (
    BedrockAccessDenied,
    GatewayAccessDenied,
    make_bedrock_llm,
    make_gateway_llm,
    make_bedrock_embeddings,
)

llm = make_bedrock_llm("qwen.qwen3-32b-v1:0", region="us-east-1", max_tokens=4096)
llm.invoke("ping")  # AccessDeniedException -> BedrockAccessDenied, naming the ARN and the IAM action

embedder = make_bedrock_embeddings("amazon.titan-embed-text-v2:0")  # bedrock extra; vectors, not messages
```

### Credentials are the caller's

`make_gateway_llm` takes `api_key` as a **required keyword argument**. It does
not read `AI_GATEWAY_API_KEY`, or any other variable, on its own initiative:

```python
gw = make_gateway_llm(
    "openai/gpt-4o",
    api_key=os.environ["AI_GATEWAY_API_KEY"],  # the CALLER's read, not ours
    credential_hint="AI_GATEWAY_API_KEY",
)
```

`credential_hint` exists because the refusal message used to name that
variable, and a band-1 package should not know it. Pass the hint and the
message reads *"Set a valid bearer key in the AI_GATEWAY_API_KEY environment
variable"*; omit it and the message names the base URL and says the supplied
key was rejected. Either way a falsy `api_key` is refused at construction
time with `GatewayAccessDenied`.

`make_bedrock_llm` takes no credentials — `boto3` resolves AWS credentials
itself, below this package.

## Error taxonomy

```
ModelsIoError
├── BedrockAccessDenied     Bedrock returned AccessDeniedException on InvokeModel
├── GatewayAccessDenied     falsy api_key at construction, or 401 at invoke
└── ProviderNotInstalled    the extra backing this constructor is absent
```

One base class, so a caller can write a single `except` clause.

## Content normalization

Bedrock's Converse API returns `response.content` as a list of content blocks
(text + reasoning) for "thinking" models, which breaks every consumer that
calls `.strip()` on it. Both guarded subclasses collapse list content to a
plain `str` and preserve the non-text blocks on
`additional_kwargs["reasoning"]` — never dropped. The trigger is content
**shape**, not model id.

## Pricing

`models_io.pricing` carries a hardcoded USD-per-million-tokens table for ~25 Bedrock and
Anthropic model IDs, and one function over it:

```python
from models_io import UnknownModelError, cost_for_usage

cost_for_usage("us.amazon.nova-pro-v1:0", {"input": 1_000_000, "output": 1_000_000})  # -> 4.0
```

`usage` keys are `input`, `output`, `cache_read`, `cache_write`; missing keys count as zero.
An unpriced model raises `UnknownModelError`, which subclasses `KeyError` so a caller that
already guards `except KeyError` needs no change.

The table lives here rather than with any one consumer because it is model-domain data:
`subagents-io` prices trace records with it, and an evaluation harness prices a sweep with it,
neither through the other. `usage` is typed `Mapping[str, int]` so `cost_for_usage` is
assignable to `subagents_io.PriceLookup` — that package injects it and never imports this one.

**Prices are updated by hand.** They are current as of 2026-05-14 (2026-05-29 for the additions
noted in the module docstring). `PRICES` is reachable at `models_io.pricing.PRICES` but is not
part of the public surface.
