"""The access-denied taxonomy, plus the message construction that goes with it.

`ModelsIoError` is the base for every failure this package raises, so a caller
can write one `except` clause and catch the lot.

Both access-denied formatters live here rather than beside the guards that use
them. `make_gateway_llm` refuses a falsy `api_key` *before* it knows whether
the `vercel` extra is installed, so the gateway message has to be reachable
without importing `models_io.vercel`; keeping the Bedrock one here too puts the
taxonomy and its message construction in one place.

No module in this package reads the environment, so no message names an
environment variable on its own initiative. `credential_hint` is how a caller
puts its own variable name back into the refusal it will show a user.
"""

from __future__ import annotations


class ModelsIoError(Exception):
    """Base for every error raised by this package."""


class BedrockAccessDenied(ModelsIoError):
    """Raised when Bedrock returns AccessDeniedException for an InvokeModel call.

    The message always names the attempted model ARN and the
    `bedrock:InvokeModel` IAM action so the user can fix permissions without a
    CloudTrail hunt.
    """


class GatewayAccessDenied(ModelsIoError):
    """Raised when the Vercel AI Gateway rejects a request for auth reasons.

    Covers two cases, both surfaced with an actionable message:
      - the caller supplied a falsy `api_key` to `make_gateway_llm`
        (preflight config error);
      - the gateway returns 401 / `openai.AuthenticationError` on invoke.

    The message always names the gateway base URL, and names the caller's
    credential variable whenever the caller supplied a `credential_hint`.
    """


class ProviderNotInstalled(ModelsIoError):
    """Raised when the extra backing a constructor is not installed.

    The provider stacks live behind `models-io[bedrock]` and
    `models-io[vercel]`, imported lazily inside the constructors. Absence is a
    typed, actionable failure naming the extra to install, not a bare
    `ImportError` from an import the caller never wrote. The original
    `ImportError` is preserved as `__cause__`.
    """


def _format_access_denied_message(model_id: str, original: Exception) -> str:
    return (
        "Bedrock access denied.\n"
        f"  Model ARN attempted: {model_id}\n"
        f"  IAM action required: bedrock:InvokeModel\n"
        "  Add an IAM policy with: "
        '{"Effect":"Allow","Action":"bedrock:InvokeModel",'
        '"Resource":"arn:aws:bedrock:*::foundation-model/*"}\n'
        f"  Original error: {original}"
    )


def _format_gateway_access_denied_message(
    base_url: str,
    original: Exception | None,
    credential_hint: str | None = None,
) -> str:
    if credential_hint is None:
        remedy = "  The supplied api_key was rejected; pass a valid bearer key for this gateway.\n"
    else:
        remedy = f"  Set a valid bearer key in the {credential_hint} environment variable.\n"
    return f"Vercel AI Gateway access denied.\n  Gateway base URL: {base_url}\n{remedy}  Original error: {original}"


def _format_missing_extra_message(extra: str, original: ImportError) -> str:
    return (
        f"The models-io[{extra}] extra is not installed.\n"
        f"  Install it with: pip install 'models-io[{extra}]'\n"
        f"  Original error: {original}"
    )
