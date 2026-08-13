"""Tests for config_io.store — the Protocol's shipped implementation."""

from __future__ import annotations

import hashlib

import pytest
import yaml
from config_fakes import DictStore, InvalidAfterWriteStore, LossyStore
from config_io.errors import StoreValidationError
from config_io.store import ConfigStore, Fingerprint, PlainYamlStore


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
    assert yaml.safe_load(store.path.read_text(encoding="utf-8")) == {
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
