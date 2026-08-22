import importlib.resources


def test_the_package_ships_no_tags_vocabulary() -> None:
    """`tags.yaml` is the vault's, not a package's: the scaffold owns it, so
    no tier-3 package writes it at all and the collision is removed rather
    than arbitrated."""
    assets = importlib.resources.files("code_wiki_okf") / "assets"
    assert not (assets / "tags.yaml").is_file()
