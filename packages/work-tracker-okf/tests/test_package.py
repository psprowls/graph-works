import work_tracker_okf


def test_version() -> None:
    """`0.2.1` gave `install_bundle` a fourth act, merging this package's
    `CONTRIBUTED_TAGS` into the bundle's `tags.yaml`; `BundleInstall` grew
    two defaulted fields and a property, so no existing caller's construction
    or reads changed. `0.2.2` adds `*/.DS_Store` to `IGNORE` -- and pointedly
    not to `ARCHIVE_IGNORE`. Both additive, so both patches (ADR-0007)."""
    assert work_tracker_okf.__version__ == "0.6.0"


def test_dependencies_are_exactly_the_three_the_spec_allows() -> None:
    """W-F: `okf-io`, `okf-ext[schemas]`, `typer` and nothing else. A fourth
    runtime dependency is a scope decision, not an implementation detail, so
    it should fail here before it fails a review."""
    import tomllib
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with manifest.open("rb") as handle:
        project = tomllib.load(handle)["project"]

    names = {entry.split(">")[0].split("<")[0].split("=")[0].strip() for entry in project["dependencies"]}
    assert names == {"okf-io", "okf-ext[schemas]", "typer"}
    assert "okf-io>=0.2.4,<0.3" in project["dependencies"]


def test_the_public_surface_is_importable_from_the_package_root() -> None:
    from work_tracker_okf import IGNORE, WorkItem, install_bundle, load_items

    assert callable(load_items)
    assert callable(install_bundle)
    assert isinstance(IGNORE, tuple)
    assert WorkItem.__name__ == "WorkItem"


def test_vocabulary_stays_a_submodule() -> None:
    """Fourteen constants read better as `vocabulary.TERMINAL_STATUSES` than
    as bare names at the package's front door."""
    import work_tracker_okf
    from work_tracker_okf import vocabulary

    assert "TYPES" not in work_tracker_okf.__all__
    assert vocabulary.TYPES


def test_py_typed_ships_inside_the_package() -> None:
    import importlib.resources

    assert (importlib.resources.files("work_tracker_okf") / "py.typed").is_file()


def test_the_module_root_is_flat_and_singular() -> None:
    """ADR-0006: one module root, no namespace nesting. The wheel target names
    exactly one package directory, and the import name is that directory."""
    import tomllib
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with manifest.open("rb") as handle:
        config = tomllib.load(handle)

    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/work_tracker_okf"]
    assert config["project"]["name"] == "work-tracker-okf"


def test_both_ignore_recipes_are_importable_from_the_package_root() -> None:
    from work_tracker_okf import ARCHIVE_IGNORE, IGNORE

    assert isinstance(ARCHIVE_IGNORE, tuple)
    assert set(ARCHIVE_IGNORE) < set(IGNORE)
