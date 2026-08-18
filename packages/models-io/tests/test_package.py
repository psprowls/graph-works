"""The package imports, is typed, and declares zero runtime dependencies."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import models_io

PKG_ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> dict:
    return tomllib.loads((PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _distribution_names(requirements: list[str]) -> set[str]:
    # "langchain-openai>=1.3.3,<2" -> "langchain-openai"
    return {re.split(r"[<>=!~\[;\s]", req)[0] for req in requirements}


def test_version_is_static():
    assert models_io.__version__ == "0.2.0"


def test_ships_a_py_typed_marker():
    assert (PKG_ROOT / "src" / "models_io" / "py.typed").is_file()


def test_declares_no_runtime_dependencies_and_exactly_two_extras():
    # A fourth provider distribution — or a base dependency — is a scope
    # decision, not an implementation detail. This is where it has to be made
    # deliberately rather than drift in behind an import.
    meta = _metadata()
    assert meta["project"]["dependencies"] == []

    extras = meta["project"]["optional-dependencies"]
    assert set(extras) == {"bedrock", "vercel"}
    assert _distribution_names(extras["bedrock"]) == {"langchain-aws", "boto3"}
    assert _distribution_names(extras["vercel"]) == {"langchain-openai", "openai"}


def test_the_error_taxonomy_has_one_base():
    # One base class so a caller can write a single `except` clause, matching
    # config-io's RegistryError habit.
    from models_io import BedrockAccessDenied, GatewayAccessDenied, ModelsIoError, ProviderNotInstalled

    for cls in (BedrockAccessDenied, GatewayAccessDenied, ProviderNotInstalled):
        assert issubclass(cls, ModelsIoError), cls.__name__
    assert issubclass(ModelsIoError, Exception)


def test_the_gateway_message_names_the_hinted_variable():
    from models_io.errors import _format_gateway_access_denied_message

    msg = _format_gateway_access_denied_message(
        "https://ai-gateway.vercel.sh/v1", None, credential_hint="AI_GATEWAY_API_KEY"
    )
    assert "Set a valid bearer key in the AI_GATEWAY_API_KEY environment variable." in msg
    assert "https://ai-gateway.vercel.sh/v1" in msg


def test_the_gateway_message_names_no_variable_without_a_hint():
    # The variable name is the caller's to supply. Without a hint this package
    # must not invent one.
    from models_io.errors import _format_gateway_access_denied_message

    msg = _format_gateway_access_denied_message("https://example.test/v1", None)
    assert "AI_GATEWAY_API_KEY" not in msg
    assert "https://example.test/v1" in msg


def test_the_missing_extra_message_names_the_extra_to_install():
    from models_io.errors import _format_missing_extra_message

    msg = _format_missing_extra_message("bedrock", ImportError("No module named 'langchain_aws'"))
    assert "models-io[bedrock]" in msg
    assert "langchain_aws" in msg


def test_all_is_sorted_and_bound():
    assert models_io.__all__ == sorted(models_io.__all__)
    for name in models_io.__all__:
        assert hasattr(models_io, name), name


def test_the_public_surface_is_the_spec_s_module_table():
    # Exactly these nine. `normalize_content` and `PRICES` are deliberately
    # absent: in both cases the chartered concept is something else (the error
    # subclasses; the pricing function), so a caller can reach them at
    # models_io.normalize / models_io.pricing without them becoming supported
    # surface.
    assert set(models_io.__all__) == {
        "BedrockAccessDenied",
        "GatewayAccessDenied",
        "ModelsIoError",
        "ProviderNotInstalled",
        "UnknownModelError",
        "cost_for_usage",
        "make_bedrock_embeddings",
        "make_bedrock_llm",
        "make_gateway_llm",
    }
