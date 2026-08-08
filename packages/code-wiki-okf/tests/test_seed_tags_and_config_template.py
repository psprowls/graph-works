import importlib.resources

from okf_ext.tags import load_vocabulary
from ruamel.yaml import YAML


def test_seed_tags_vocabulary_loads_empty() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_tags.yaml"
    vocabulary = load_vocabulary(str(assets))
    assert vocabulary.allowed == frozenset()


def test_seed_repositories_template_is_empty_but_structured() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_repositories.yaml"
    text = assets.read_text(encoding="utf-8")
    doc = YAML(typ="safe").load(text)
    assert doc["graph_dir"] == "../graphs/code"
    assert doc["repositories"] == {}
    assert doc["ignore"] == []
