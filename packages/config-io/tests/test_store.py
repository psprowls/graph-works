"""Tests for config_io.store — the Protocol's shipped implementation."""

from __future__ import annotations

import hashlib

import pytest
from config_fakes import DictStore, InvalidAfterWriteStore, LossyStore
from config_io.errors import StoreValidationError
from config_io.store import ConfigStore, Fingerprint, LayeredStore, LayeredYamlStore, PlainYamlStore


def _store(tmp_path, text=None):
    path = tmp_path / "config.yaml"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return PlainYamlStore(path)


def test_plain_yaml_store_satisfies_the_protocol(tmp_path):
    store: ConfigStore = _store(tmp_path)
    assert store.read() == {}


@pytest.mark.parametrize("fake", [DictStore(), LossyStore(allowed=()), InvalidAfterWriteStore("k", 1)])
def test_fakes_satisfy_the_protocol(fake):
    store: ConfigStore = fake
    assert isinstance(store.read(), dict)


def test_missing_file_reads_empty(tmp_path):
    assert _store(tmp_path).read() == {}


def test_empty_file_reads_empty(tmp_path):
    assert _store(tmp_path, "").read() == {}


def test_null_document_reads_empty(tmp_path):
    assert _store(tmp_path, "null\n").read() == {}


def test_read_and_read_explicit_agree(tmp_path):
    # PlainYamlStore is lossless: no defaults injected, no normalization, so
    # registry's persistence check can never fire against it.
    store = _store(tmp_path, "topic: My Wiki\nstate_gate:\n  enabled: true\n")
    assert store.read() == store.read_explicit() == {"topic": "My Wiki", "state_gate": {"enabled": True}}


def test_malformed_yaml_raises_store_validation_error(tmp_path):
    with pytest.raises(StoreValidationError, match="valid YAML"):
        _store(tmp_path, "a: [1, 2\n").read()


def test_non_mapping_top_level_raises_store_validation_error(tmp_path):
    with pytest.raises(StoreValidationError, match="mapping"):
        _store(tmp_path, "- one\n- two\n").read()


def test_write_creates_parents_and_round_trips(tmp_path):
    store = PlainYamlStore(tmp_path / "nested" / "deep" / "config.yaml")
    store.write({"topic": "My Wiki", "roles": {"scanner": {"max_tokens": 512}}})
    assert PlainYamlStore(store.path).read() == {
        "topic": "My Wiki",
        "roles": {"scanner": {"max_tokens": 512}},
    }
    assert store.read() == {"topic": "My Wiki", "roles": {"scanner": {"max_tokens": 512}}}


def test_snapshot_is_none_when_nothing_is_stored(tmp_path):
    assert _store(tmp_path).snapshot() is None


def test_restore_none_removes_the_file(tmp_path):
    store = _store(tmp_path, "topic: x\n")
    store.restore(None)
    assert not store.path.exists()


def test_restore_none_on_an_already_absent_file_is_a_no_op(tmp_path):
    store = _store(tmp_path)
    store.restore(None)
    assert not store.path.exists()


def test_snapshot_restore_round_trips_bytes_verbatim(tmp_path):
    store = _store(tmp_path, "topic: x # a comment survives\n")
    snap = store.snapshot()
    store.write({"topic": "y"})
    store.restore(snap)
    assert store.path.read_bytes() == snap


def test_fingerprint_is_none_when_nothing_is_stored(tmp_path):
    assert _store(tmp_path).fingerprint() is None


def test_fingerprint_carries_mtime_and_sha256(tmp_path):
    store = _store(tmp_path, "topic: x\n")
    fp = store.fingerprint()
    assert isinstance(fp, Fingerprint)
    assert fp.sha256 == hashlib.sha256(store.path.read_bytes()).hexdigest()
    assert fp.mtime == pytest.approx(store.path.stat().st_mtime)


def test_fingerprint_of_an_empty_file_is_real_not_none(tmp_path):
    # The file exists, so both fields must be real — never a mix of None and
    # not-None, which is what makes a staleness check trustworthy.
    fp = _store(tmp_path, "").fingerprint()
    assert fp is not None
    assert fp.sha256 == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("enabled: yes\n", {"enabled": "yes"}),
        ("enabled: off\n", {"enabled": "off"}),
        ("n: 012\n", {"n": 12}),
        ("t: 1:30\n", {"t": "1:30"}),
    ],
)
def test_the_store_reads_yaml_1_2_not_yaml_1_1(tmp_path, text, expected):
    # D-001: `load_config`'s ruamel parse is authoritative, and the store
    # matches it. Under YAML 1.1 these read as True, False, 10 and 90 -- the
    # three rules 1.2 dropped, and the exact divergence this item removes.
    assert _store(tmp_path, text).read() == expected


def test_write_preserves_insertion_order(tmp_path):
    # D-003: ruamel's safe representer sorts mapping keys by default. Left
    # alone, the first `gw config set` reorders the whole manifest.
    store = PlainYamlStore(tmp_path / "config.yaml")
    store.write(
        {
            "version": 1,
            "topic": "graph-works",
            "repositories": {"gw": {"path": "../.."}},
            "ignore": ["tmp/**", "*.lock"],
        }
    )
    text = store.path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "version: 1"
    assert [line for line in text.splitlines() if not line.startswith((" ", "-"))] == [
        "version: 1",
        "topic: graph-works",
        "repositories:",
        "ignore:",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "yes",  # a YAML-1.1 boolean-ish string: loses its quotes
        "Déjà vu",  # non-ASCII: emitted literally, not \xNN-escaped
        "line1\nline2\n",  # embedded newlines: double-quoted, not padded
        "x" * 200,  # no break opportunity: moves to a continuation line
    ],
    ids=["boolean-ish", "non-ascii", "embedded-newline", "unbreakable-long"],
)
def test_the_four_reformatted_scalars_round_trip_to_the_same_value(tmp_path, value):
    # These four emit differently than PyYAML did. All four must read back
    # identical -- the reformat is cosmetic, the value is the contract.
    store = PlainYamlStore(tmp_path / "config.yaml")
    store.write({"k": value})
    assert store.read() == {"k": value}


# --- LayeredYamlStore ------------------------------------------------------


def _layered(tmp_path, base_text=None, overlay_text=None):
    base = tmp_path / "config.yaml"
    overlay = tmp_path / "config.local.yaml"
    if base_text is not None:
        base.write_text(base_text, encoding="utf-8")
    if overlay_text is not None:
        overlay.write_text(overlay_text, encoding="utf-8")
    return LayeredYamlStore(base=PlainYamlStore(base), overlay=PlainYamlStore(overlay))


def test_layered_store_satisfies_the_config_store_protocol(tmp_path):
    store: ConfigStore = _layered(tmp_path)
    assert store.read() == {}


def test_layered_store_merges_the_overlay_over_the_base(tmp_path):
    store = _layered(
        tmp_path,
        "topic: base\nrepositories:\n  gw:\n    path: ../gw\n",
        "repositories:\n  gw:\n    path: /abs/gw\n",
    )
    assert store.read_explicit() == {"topic": "base", "repositories": {"gw": {"path": "/abs/gw"}}}


def test_layered_read_and_read_explicit_agree(tmp_path):
    # Lossless like PlainYamlStore: nothing injected, nothing normalised.
    store = _layered(tmp_path, "topic: base\n", "topic: local\n")
    assert store.read() == store.read_explicit() == {"topic": "local"}


def test_an_absent_overlay_reads_as_the_base_alone(tmp_path):
    store = _layered(tmp_path, "topic: base\n")
    assert store.read_explicit() == {"topic": "base"}
    assert store.read_overlay_explicit() == {}


def test_the_layers_are_readable_separately(tmp_path):
    store = _layered(tmp_path, "topic: base\n", "topic: local\n")
    assert store.read_base_explicit() == {"topic": "base"}
    assert store.read_overlay_explicit() == {"topic": "local"}


def test_invalid_overlay_yaml_names_the_overlay_path(tmp_path):
    store = _layered(tmp_path, "topic: base\n", "a: [1, 2\n")
    with pytest.raises(StoreValidationError, match=r"config\.local\.yaml"):
        store.read_explicit()


def test_invalid_base_yaml_still_names_the_base_path(tmp_path):
    store = _layered(tmp_path, "a: [1, 2\n", "topic: local\n")
    with pytest.raises(StoreValidationError, match=r"config\.yaml"):
        store.read_explicit()


@pytest.mark.parametrize(
    ("method", "args"),
    [("write", ({},)), ("snapshot", ()), ("restore", (None,))],
)
def test_the_layered_store_refuses_every_write(tmp_path, method, args):
    # D-002: set_key is read-mutate-write over store.read(); pointed at the
    # merged view it would copy overlay values into the committed file.
    store = _layered(tmp_path, "topic: base\n")
    with pytest.raises(TypeError, match=r"read-only.*\.base.*\.overlay"):
        getattr(store, method)(*args)


def test_the_fingerprint_is_the_bases(tmp_path):
    store = _layered(tmp_path, "topic: base\n", "topic: local\n")
    base_bytes = (tmp_path / "config.yaml").read_bytes()
    assert store.fingerprint().sha256 == hashlib.sha256(base_bytes).hexdigest()


def test_the_overlay_fingerprint_is_the_overlays(tmp_path):
    store = _layered(tmp_path, "topic: base\n", "topic: local\n")
    overlay_bytes = (tmp_path / "config.local.yaml").read_bytes()
    assert store.overlay_fingerprint().sha256 == hashlib.sha256(overlay_bytes).hexdigest()


def test_an_absent_overlay_has_no_fingerprint(tmp_path):
    assert _layered(tmp_path, "topic: base\n").overlay_fingerprint() is None


def test_an_absent_base_has_no_fingerprint(tmp_path):
    assert _layered(tmp_path, None, "topic: local\n").fingerprint() is None


def test_only_the_layered_store_is_a_layered_store(tmp_path):
    # The Protocol is how registry and projection tell the two apart without
    # either learning a file name.
    assert isinstance(_layered(tmp_path), LayeredStore)
    assert not isinstance(PlainYamlStore(tmp_path / "config.yaml"), LayeredStore)
    assert not isinstance(DictStore(), LayeredStore)
