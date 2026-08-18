"""The embeddings half of the Bedrock seam.

The third module in this package that imports a provider stack, and the
thinnest. `BedrockEmbeddings` returns `list[float]` rather than a message whose
content might be a block list, so there is nothing to normalize and no
subclass here — the guarded-subclass strategy `bedrock.py` uses exists for
content normalization, which does not apply.

Reached only through `models_io.loader.make_bedrock_embeddings`, which imports
this module lazily inside the function body. `import models_io` still loads no
provider stack.
"""

from __future__ import annotations

from langchain_aws import BedrockEmbeddings


def build(model_id: str, *, region: str = "us-east-1", normalize: bool = True) -> BedrockEmbeddings:
    """Construct a Bedrock embeddings client from explicit config.

    No credentials argument: `boto3` resolves AWS credentials itself, below
    this package.
    """
    return BedrockEmbeddings(model_id=model_id, region_name=region, normalize=normalize)
