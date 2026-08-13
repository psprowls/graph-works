"""The extras actually isolate.

Two properties, and they need different machinery. Absence is forced in-process
with the `sys.modules[name] = None` trick — the import system raises on a None
entry — but the *cleanliness* check has to be a subprocess, because by the time
this file runs, test_bedrock.py and test_vercel.py have already imported both
stacks into this interpreter.
"""

from __future__ import annotations

import json
import subprocess
import sys

import models_io
import pytest
from models_io import ProviderNotInstalled, make_bedrock_llm, make_gateway_llm

PROVIDER_ROOTS = ("boto3", "botocore", "langchain_aws", "langchain_openai", "openai")


def _hide(monkeypatch, provider_root: str, submodule: str) -> None:
    """Make `from models_io import <submodule>` fail as if the extra were absent.

    Three moves, all of them necessary: the None entry is what makes the
    provider import raise, and the cached submodule plus the attribute already
    bound on the package are what would otherwise let the import succeed from
    cache without ever reaching the provider.
    """
    monkeypatch.setitem(sys.modules, provider_root, None)
    monkeypatch.delitem(sys.modules, f"models_io.{submodule}", raising=False)
    monkeypatch.delattr(models_io, submodule, raising=False)


def test_make_bedrock_llm_without_the_extra_raises_provider_not_installed(monkeypatch):
    _hide(monkeypatch, "langchain_aws", "bedrock")

    with pytest.raises(ProviderNotInstalled) as exc_info:
        make_bedrock_llm("some-model")

    msg = str(exc_info.value)
    assert "models-io[bedrock]" in msg
    assert isinstance(exc_info.value.__cause__, ImportError)


def test_make_gateway_llm_without_the_extra_raises_provider_not_installed(monkeypatch):
    _hide(monkeypatch, "langchain_openai", "vercel")

    # A real key, so the falsy-key refusal cannot mask the missing extra.
    with pytest.raises(ProviderNotInstalled) as exc_info:
        make_gateway_llm("some/model", api_key="test-key")

    msg = str(exc_info.value)
    assert "models-io[vercel]" in msg
    assert isinstance(exc_info.value.__cause__, ImportError)


def test_importing_the_package_loads_no_provider_stack():
    # A subprocess, because in-process the other test files have already
    # imported both stacks.
    roots = json.dumps(list(PROVIDER_ROOTS))
    code = (
        "import json, sys\n"
        "import models_io\n"
        f"roots = set({roots})\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in roots)))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout) == []
