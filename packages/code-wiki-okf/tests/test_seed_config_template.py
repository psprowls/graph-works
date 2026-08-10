import importlib.resources

from ruamel.yaml import YAML


def test_seed_repositories_template_is_empty_but_structured() -> None:
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_repositories.yaml"
    text = assets.read_text(encoding="utf-8")
    doc = YAML(typ="safe").load(text)
    assert doc["graph_dir"] == "../graphs/code"
    assert doc["repositories"] == {}
    assert doc["ignore"] == []


def test_the_template_documents_declarations_dir_without_setting_it() -> None:
    """Absent means "in the bundle". The key is commented so a human reading
    their own config learns it exists without the bundle silently acquiring a
    relocation nobody asked for."""
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_repositories.yaml"
    text = assets.read_text(encoding="utf-8")
    assert "# declarations_dir:" in text
    assert YAML(typ="safe").load(text).get("declarations_dir") is None


def test_the_package_ships_no_tags_vocabulary() -> None:
    """`_tags.yaml` is the vault's, not a package's: the scaffold owns it, so
    no tier-3 package writes it at all and the collision is removed rather
    than arbitrated."""
    assets = importlib.resources.files("code_wiki_okf") / "assets"
    assert not (assets / "_tags.yaml").is_file()
