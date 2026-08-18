"""Generic constructor tests — make_bedrock_llm / make_gateway_llm / make_bedrock_embeddings."""

from __future__ import annotations

import pytest
from langchain_aws import BedrockEmbeddings
from models_io import GatewayAccessDenied, make_bedrock_embeddings, make_bedrock_llm, make_gateway_llm
from models_io.bedrock import _GuardedChatBedrockConverse
from models_io.vercel import _GuardedChatOpenAI

DEFAULT_GATEWAY_URL = "https://ai-gateway.vercel.sh/v1"


def test_make_bedrock_llm_binds_model_id_and_region() -> None:
    llm = make_bedrock_llm("anthropic.fake-model", region="us-west-2", max_tokens=64)
    assert isinstance(llm, _GuardedChatBedrockConverse)
    assert llm._model_id_for_errors == "anthropic.fake-model"
    assert llm.region_name == "us-west-2"
    assert llm.max_tokens == 64


def test_make_bedrock_llm_defaults() -> None:
    llm = make_bedrock_llm("m")
    assert llm.region_name == "us-east-1"


def test_make_gateway_llm_refuses_a_falsy_api_key() -> None:
    # The refusal happens before the provider module is imported, which is why
    # the message formatter lives in errors.py rather than beside the guard.
    with pytest.raises(GatewayAccessDenied) as hinted:
        make_gateway_llm("some/model", api_key="", credential_hint="AI_GATEWAY_API_KEY")
    assert "AI_GATEWAY_API_KEY" in str(hinted.value)

    with pytest.raises(GatewayAccessDenied) as unhinted:
        make_gateway_llm("some/model", api_key="")
    assert "AI_GATEWAY_API_KEY" not in str(unhinted.value)
    assert DEFAULT_GATEWAY_URL in str(unhinted.value)


def test_make_gateway_llm_builds_when_key_present() -> None:
    llm = make_gateway_llm("some/model", api_key="test-key", max_tokens=128)
    assert isinstance(llm, _GuardedChatOpenAI)
    assert llm._base_url_for_errors == DEFAULT_GATEWAY_URL
    assert llm.model_name == "some/model"
    assert llm.max_tokens == 128

    # max_tokens omitted is the other branch of `build`'s kwargs assembly, and
    # the hint reaches the instance that will raise on a 401.
    bare = make_gateway_llm("some/model", api_key="test-key", credential_hint="MY_KEY")
    assert bare._credential_hint_for_errors == "MY_KEY"


def test_make_bedrock_embeddings_binds_model_id_region_and_normalize() -> None:
    embedder = make_bedrock_embeddings("amazon.titan-embed-text-v2:0", region="us-west-2", normalize=False)
    assert isinstance(embedder, BedrockEmbeddings)
    assert embedder.model_id == "amazon.titan-embed-text-v2:0"
    assert embedder.region_name == "us-west-2"
    assert embedder.normalize is False


def test_make_bedrock_embeddings_defaults() -> None:
    embedder = make_bedrock_embeddings("amazon.titan-embed-text-v2:0")
    assert embedder.region_name == "us-east-1"
    assert embedder.normalize is True
