"""The Bedrock half of `content-normalizing-model-subclasses`.

One of exactly two modules in this package that import a provider stack.
Reached only through `models_io.loader.make_bedrock_llm`, which imports it
lazily inside the function body — so nothing here loads unless the caller
actually asks for a Bedrock model and the `bedrock` extra is installed.

Strategy choice: `ChatBedrockConverse` is a Pydantic v2 BaseModel with
`extra='forbid'` and `__slots__`, so direct attribute reassignment
(`llm.invoke = fn`) is rejected with ValueError. We therefore use the
subclass-override strategy: `_GuardedChatBedrockConverse` overrides `invoke`
to apply the try/except wrapper, and exposes the parent call as
`_original_invoke` so unit tests can stub it via `monkeypatch`.
"""

from __future__ import annotations

from typing import Any

import botocore.exceptions
from langchain_aws import ChatBedrockConverse

from models_io.errors import BedrockAccessDenied, _format_access_denied_message
from models_io.normalize import normalize_content


class _GuardedChatBedrockConverse(ChatBedrockConverse):
    """ChatBedrockConverse subclass that translates AccessDeniedException and
    normalizes list-shaped content.

    `invoke()`/`ainvoke()` defer to the parent implementation via
    `_original_invoke`/`_original_ainvoke` so unit tests can monkeypatch the
    inner call without touching the network. Both paths translate
    AccessDeniedException → BedrockAccessDenied and pass successful responses
    through `normalize_content`.

    The leading underscore is deliberate: this is a monkeypatch seam for the
    tests, not API.
    """

    # Default ARN for error messages; overridden per-instance by `build` via
    # `object.__setattr__` (Pydantic v2 forbids normal field assignment).
    _model_id_for_errors: str = ""

    def _wrap_client_error(self, e: botocore.exceptions.ClientError) -> BedrockAccessDenied | None:
        """Translate an AccessDeniedException ClientError into a
        BedrockAccessDenied, or return None for any other error code (caller
        re-raises the original)."""
        if e.response.get("Error", {}).get("Code") == "AccessDeniedException":
            return BedrockAccessDenied(_format_access_denied_message(self._model_id_for_errors, e))
        return None

    def _original_invoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- mirrors ChatBedrockConverse.invoke's own signature
        return ChatBedrockConverse.invoke(self, *args, **kwargs)

    async def _original_ainvoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- mirrors ChatBedrockConverse.ainvoke's own signature
        return await ChatBedrockConverse.ainvoke(self, *args, **kwargs)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- overrides ChatBedrockConverse.invoke; must accept whatever the parent accepts
        try:
            response = self._original_invoke(*args, **kwargs)
        except botocore.exceptions.ClientError as e:
            wrapped = self._wrap_client_error(e)
            if wrapped is not None:
                raise wrapped from e
            raise
        return normalize_content(response)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- overrides ChatBedrockConverse.ainvoke; must accept whatever the parent accepts
        try:
            response = await self._original_ainvoke(*args, **kwargs)
        except botocore.exceptions.ClientError as e:
            wrapped = self._wrap_client_error(e)
            if wrapped is not None:
                raise wrapped from e
            raise
        return normalize_content(response)


def build(model_id: str, *, region: str = "us-east-1", max_tokens: int | None = None) -> ChatBedrockConverse:
    """Construct a guarded Bedrock Converse chat model from explicit config.

    No credentials argument: `boto3` resolves AWS credentials itself, below
    this package.
    """
    kwargs: dict[str, Any] = dict(model=model_id, region_name=region)
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    llm = _GuardedChatBedrockConverse(**kwargs)
    # Bind the ARN to the instance so error messages name the exact model.
    # object.__setattr__ bypasses Pydantic v2's extra='forbid' validator.
    object.__setattr__(llm, "_model_id_for_errors", model_id)
    return llm
