"""The Vercel AI Gateway half of `content-normalizing-model-subclasses`.

The second of exactly two modules in this package that import a provider stack,
and symmetric with `models_io.bedrock`. Reached only through
`models_io.loader.make_gateway_llm`, which imports it lazily inside the
function body.

`_credential_hint_for_errors` is the one thing this module has that its source
did not: the refusal message used to name `AI_GATEWAY_API_KEY` because this
package read that variable. It no longer does, so the caller hands the name
back — or does not, and the message names only the base URL.
"""

from __future__ import annotations

from typing import Any

import openai
from langchain_openai import ChatOpenAI

from models_io.errors import GatewayAccessDenied, _format_gateway_access_denied_message
from models_io.normalize import normalize_content


class _GuardedChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass for the Vercel AI Gateway that translates gateway
    auth failures into GatewayAccessDenied and normalizes list-shaped content.

    Symmetric with `_GuardedChatBedrockConverse`: `invoke()`/`ainvoke()` defer
    to the parent via `_original_invoke`/`_original_ainvoke` (the monkeypatch
    seam), translate `openai.AuthenticationError` → GatewayAccessDenied, and
    pass successful responses through the shared `normalize_content`. All other
    errors propagate raw (the gateway path is opt-in; debuggers see real errors).

    The leading underscore is deliberate: monkeypatch seam, not API.
    """

    # Bound per-instance by `build` via `object.__setattr__` (Pydantic v2
    # forbids normal field assignment).
    _base_url_for_errors: str = ""
    _credential_hint_for_errors: str | None = None

    def _denied(self, original: Exception) -> GatewayAccessDenied:
        return GatewayAccessDenied(
            _format_gateway_access_denied_message(self._base_url_for_errors, original, self._credential_hint_for_errors)
        )

    def _original_invoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- mirrors ChatOpenAI.invoke's own signature
        return ChatOpenAI.invoke(self, *args, **kwargs)

    async def _original_ainvoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- mirrors ChatOpenAI.ainvoke's own signature
        return await ChatOpenAI.ainvoke(self, *args, **kwargs)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- overrides ChatOpenAI.invoke; must accept whatever the parent accepts
        try:
            response = self._original_invoke(*args, **kwargs)
        except openai.AuthenticationError as e:
            raise self._denied(e) from e
        return normalize_content(response)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- overrides ChatOpenAI.ainvoke; must accept whatever the parent accepts
        try:
            response = await self._original_ainvoke(*args, **kwargs)
        except openai.AuthenticationError as e:
            raise self._denied(e) from e
        return normalize_content(response)


def build(
    model_id: str,
    *,
    api_key: str,
    base_url: str,
    credential_hint: str | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """Construct a guarded Vercel AI Gateway chat model from explicit config.

    Every credential is an argument. `make_gateway_llm` has already refused a
    falsy `api_key` before this is reached.
    """
    kwargs: dict[str, Any] = dict(model=model_id, api_key=api_key, base_url=base_url)
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    llm = _GuardedChatOpenAI(**kwargs)
    object.__setattr__(llm, "_base_url_for_errors", base_url)
    object.__setattr__(llm, "_credential_hint_for_errors", credential_hint)
    return llm
