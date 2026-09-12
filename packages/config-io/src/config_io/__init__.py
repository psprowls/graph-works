"""config-io: schema-driven, git-like scoped configuration over YAML.

Band 1. This package never discovers a workspace, never reads the process
environment on its own initiative, and never knows a file or directory name.
The catalog, the store, the environment mapping and the projection target are
all supplied by the caller.

    from pathlib import Path
    from config_io import ConfigEntry, PlainYamlStore, resolve_key, set_key

    catalog = (ConfigEntry(key="topic", type="str", default=None, description="Display name."),)
    store = PlainYamlStore(Path("config.yaml"))
    set_key(catalog, "topic", "My Wiki", store=store, projection=Path("config.json"))
    resolve_key(catalog, "topic", store=store, environ={}).value  # "My Wiki"
"""

from __future__ import annotations

__version__ = "0.2.0"

from config_io import dotted
from config_io.entries import (
    WRITE_POLICIES,
    ConfigEntry,
    Resolved,
    WritePolicy,
    coerce,
    find_entry,
)
from config_io.errors import (
    EnvOnlyKeyError,
    InvalidValueError,
    LinkFileKeyError,
    ProvenanceKeyError,
    ReadOnlyKeyError,
    RegistryError,
    SecretKeyError,
    StoreValidationError,
    UnknownKeyError,
)
from config_io.projection import PROJECTION_FILENAME, write_projection
from config_io.registry import (
    expand_wildcards,
    resolve_all,
    resolve_key,
    set_key,
    unset_key,
)
from config_io.store import ConfigStore, Fingerprint, LayeredStore, LayeredYamlStore, PlainYamlStore

__all__ = [  # noqa: RUF022 -- sorted with plain `sorted()`, not isort's natural sort; test_all_is_sorted_and_bound holds this.
    "ConfigEntry",
    "ConfigStore",
    "EnvOnlyKeyError",
    "Fingerprint",
    "InvalidValueError",
    "LayeredStore",
    "LayeredYamlStore",
    "LinkFileKeyError",
    "PROJECTION_FILENAME",
    "PlainYamlStore",
    "ProvenanceKeyError",
    "ReadOnlyKeyError",
    "RegistryError",
    "Resolved",
    "SecretKeyError",
    "StoreValidationError",
    "UnknownKeyError",
    "WRITE_POLICIES",
    "WritePolicy",
    "coerce",
    "dotted",
    "expand_wildcards",
    "find_entry",
    "resolve_all",
    "resolve_key",
    "set_key",
    "unset_key",
    "write_projection",
]
