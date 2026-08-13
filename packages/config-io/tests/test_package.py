"""The package imports, is typed, and declares exactly one runtime dependency."""

from __future__ import annotations

import tomllib
from pathlib import Path

import config_io

PKG_ROOT = Path(__file__).resolve().parents[1]


def test_version_is_static():
    assert config_io.__version__ == "0.1.0"


def test_ships_a_py_typed_marker():
    assert (PKG_ROOT / "src" / "config_io" / "py.typed").is_file()


def test_declares_exactly_one_runtime_dependency():
    # A second runtime dependency is a scope decision, not an implementation
    # detail — this test is where that decision has to be made deliberately.
    meta = tomllib.loads((PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = meta["project"]["dependencies"]
    assert len(deps) == 1
    assert deps[0].startswith("pyyaml")


def test_all_is_sorted_and_bound():
    assert config_io.__all__ == sorted(config_io.__all__)
    for name in config_io.__all__:
        assert hasattr(config_io, name), name


def test_the_public_surface_is_the_spec_s_module_table():
    # Every name the design spec's module table promises, importable straight
    # from the package rather than from its modules.
    promised = {
        "RegistryError",
        "UnknownKeyError",
        "EnvOnlyKeyError",
        "SecretKeyError",
        "LinkFileKeyError",
        "ProvenanceKeyError",
        "ReadOnlyKeyError",
        "InvalidValueError",
        "StoreValidationError",
        "ConfigEntry",
        "WritePolicy",
        "Resolved",
        "find_entry",
        "coerce",
        "ConfigStore",
        "Fingerprint",
        "PlainYamlStore",
        "resolve_key",
        "resolve_all",
        "expand_wildcards",
        "set_key",
        "unset_key",
        "write_projection",
        "PROJECTION_FILENAME",
    }
    assert promised <= set(config_io.__all__)
