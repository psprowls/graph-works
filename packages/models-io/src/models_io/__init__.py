"""models-io: guarded Bedrock and Vercel AI Gateway chat-model constructors.

Band 1. This package never reads the process environment, never discovers a
workspace, and never knows a file or directory name. Gateway credentials are
arguments; AWS credentials are resolved by `boto3`, below this package.

    from models_io import make_bedrock_llm, make_gateway_llm

    llm = make_bedrock_llm("qwen.qwen3-32b-v1:0", region="us-east-1")
    gw = make_gateway_llm("openai/gpt-4o", api_key=key, credential_hint="AI_GATEWAY_API_KEY")

Neither constructor imports its provider stack until it is called, so
`import models_io` costs nothing beyond stdlib and installing one extra never
drags in the other.

The price table lives here too — `cost_for_usage` is model-domain data, and its
consumers do not all have a subagent pool. `subagents-io` reaches it through an
injected callable, never an import.
"""

from __future__ import annotations

__version__ = "0.1.1"

from models_io.errors import (
    BedrockAccessDenied,
    GatewayAccessDenied,
    ModelsIoError,
    ProviderNotInstalled,
)
from models_io.loader import make_bedrock_llm, make_gateway_llm
from models_io.pricing import UnknownModelError, cost_for_usage

__all__ = [
    "BedrockAccessDenied",
    "GatewayAccessDenied",
    "ModelsIoError",
    "ProviderNotInstalled",
    "UnknownModelError",
    "cost_for_usage",
    "make_bedrock_llm",
    "make_gateway_llm",
]
