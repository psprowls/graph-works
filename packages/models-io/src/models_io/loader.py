"""`provider-chat-model-constructors` — the two functions a caller needs.

Neither constructor imports its provider stack at module level. The import
happens inside the function body, so `import models_io` costs nothing beyond
stdlib and installing `models-io[bedrock]` never drags in the Vercel stack (or
the reverse). An absent extra is a typed `ProviderNotInstalled` naming the
extra to install, not a bare `ImportError` from an import the caller never
wrote.

`BaseChatModel` appears only as a return annotation, so it is imported under
`TYPE_CHECKING` — which is what lets this package declare zero base runtime
dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from models_io.errors import (
    GatewayAccessDenied,
    ProviderNotInstalled,
    _format_gateway_access_denied_message,
    _format_missing_extra_message,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

_DEFAULT_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"


def make_bedrock_llm(model_id: str, *, region: str = "us-east-1", max_tokens: int | None = None) -> BaseChatModel:
    """Build a guarded Bedrock Converse chat model from explicit config.

    The returned model translates AccessDeniedException -> BedrockAccessDenied
    and normalizes list-shaped content. No role concept -- resolving a logical
    role to concrete config is the caller's responsibility.

    Requires the `bedrock` extra; raises `ProviderNotInstalled` without it.
    AWS credentials are `boto3`'s to resolve, below this package.
    """
    try:
        from models_io import bedrock
    except ImportError as e:
        raise ProviderNotInstalled(_format_missing_extra_message("bedrock", e)) from e
    return bedrock.build(model_id, region=region, max_tokens=max_tokens)


def make_gateway_llm(
    model_id: str,
    *,
    api_key: str,
    base_url: str = _DEFAULT_GATEWAY_BASE_URL,
    credential_hint: str | None = None,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Build a guarded Vercel AI Gateway chat model from explicit config.

    Credentials are the caller's: `api_key` is required and keyword-only, and
    no module in this package reads the environment. A falsy `api_key` is
    refused here, at construction time, with `GatewayAccessDenied` — the
    preflight half of that exception's two documented cases.

    `credential_hint` names the caller's own credential variable so the refusal
    message can tell a user where to fix it (e.g.
    `credential_hint="AI_GATEWAY_API_KEY"`). Omitted, the message names the
    base URL and says the supplied key was rejected.

    Requires the `vercel` extra; raises `ProviderNotInstalled` without it.
    """
    if not api_key:
        raise GatewayAccessDenied(_format_gateway_access_denied_message(base_url, None, credential_hint))

    try:
        from models_io import vercel
    except ImportError as e:
        raise ProviderNotInstalled(_format_missing_extra_message("vercel", e)) from e
    return vercel.build(
        model_id,
        api_key=api_key,
        base_url=base_url,
        credential_hint=credential_hint,
        max_tokens=max_tokens,
    )
