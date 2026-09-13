"""Untrusted persisted records refuse before they can authorize mutation."""

import hashlib
import json

import pytest
from helpers import selection, write_skill
from plugin_fork_io import Roots, Services, SourceSpec, load_ledger, load_preview, plan_fork
from plugin_fork_io.previews import PreviewError
from test_updates import forked


def reseal_preview(path, mutate):
    envelope = json.loads(path.read_bytes())
    mutate(envelope["preview"])
    canonical = json.dumps(envelope["preview"], sort_keys=True, separators=(",", ":")).encode()
    envelope["digest"] = hashlib.sha256(canonical).hexdigest()
    path.write_bytes(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("schema_version", 2, "schema"),
        ("id", "other", "identity"),
        ("state_root", "/other-state", "identity"),
        ("operation", "publish", "operation"),
        ("content_root", "relative", "absolute"),
        ("expected_absences", ["relative"], "absolute"),
        ("candidate_path", "/other-candidate", "Candidate path"),
        ("base_digest", "0" * 64, "base digest"),
        ("lock_stores", ["relative"], "absolute"),
        ("inventory_scopes", [["relative", ["."]]], "absolute"),
        ("inventory_scopes", [["/absolute", ["../escape"]]], "path"),
        ("selection", None, "record"),
        ("allowed", 1, "type"),
        ("variant_ids", "not-array", "array"),
        ("expected_generations", [["variant"]], "array size"),
        ("source", [], "record"),
        ("review_state", "completed_clean", "literal"),
        ("source_mappings", [{"source": "../escape", "destination": "safe"}], "path"),
    ],
)
def test_tampered_preview_contract_refuses_even_with_recomputed_digest(tmp_path, field, value, reason):
    source = tmp_path / "source"
    write_skill(source, "review")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    preview = plan_fork(
        SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local()
    )
    path = roots.state / "previews" / preview.preview_id / "preview.json"
    reseal_preview(path, lambda data: data.__setitem__(field, value))
    with pytest.raises(ValueError, match=reason):
        load_preview(roots.state, preview.preview_id)
    assert not roots.content.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", 0),
        ("schema_version", 2),
        ("variant_id", "foreign"),
        ("content_root", "/absolute"),
        ("base_digest", None),
        ("origin", "uncertain"),
        ("components", [{"source": "skills/review", "name": "Bad_Name"}]),
        ("history", ["nested/id"]),
        ("files", [{"path": "safe", "hash": "bad", "kind": "file", "mode": 420}]),
        ("files", [{"path": "../escape", "hash": "0" * 64, "kind": "file", "mode": 420}]),
        ("mappings", [{"source": "safe", "destination": "../escape"}]),
    ],
)
def test_invalid_portable_ledger_refuses_without_rewriting_content(tmp_path, field, value):
    roots, variant = forked(tmp_path)
    path = roots.state / "forks" / variant / "ledger.json"
    ledger = json.loads(path.read_bytes())
    ledger[field] = value
    path.write_bytes(json.dumps(ledger).encode())
    before = (roots.content / "local-review/SKILL.md").read_bytes()
    with pytest.raises(ValueError):
        load_ledger(roots.state, variant)
    assert (roots.content / "local-review/SKILL.md").read_bytes() == before


@pytest.mark.parametrize("payload", [b"[]", b'{"record":{}}', b'{"digest":"bad","record":{}}'])
def test_invalid_sealed_binding_refuses(tmp_path, payload):
    from plugin_fork_io.store import load_binding

    path = tmp_path / "bindings/test.json"
    path.parent.mkdir()
    path.write_bytes(payload)
    with pytest.raises(PreviewError):
        load_binding(tmp_path, "test")


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "arbitrary"),
        ("start", -1),
        ("end", 0),
        ("expected", 7),
        ("replacement", None),
        ("expected", "zz"),
        ("expected", "ffff"),
        ("path", "../escape"),
    ],
)
def test_selection_adaptation_schema_rejects_invalid_byte_authority(field, value):
    from plugin_fork_io import Selection

    edit = {
        "kind": "invocation",
        "path": "skill/SKILL.md",
        "start": 0,
        "end": 1,
        "expected": "61",
        "replacement": "62",
        "purpose": "Approved name",
    }
    edit[field] = value
    with pytest.raises(ValueError):
        Selection.from_json(
            {
                "skills": [{"source": "skill", "name": "skill"}],
                "resources": [],
                "dependencies": [],
                "adaptations": [edit],
                "source_links": [],
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "arbitrary"),
        ("evidence", "guess"),
        ("required", 1),
        ("satisfied", None),
        ("group", []),
        ("start", 3),
        ("line", False),
    ],
)
def test_selection_dependency_schema_preserves_boolean_and_evidence_contract(field, value):
    from plugin_fork_io import Selection

    dependency = {
        "kind": "required-skill",
        "target": "review",
        "path": "skill/SKILL.md",
        "line": 1,
        "start": 0,
        "end": 1,
        "evidence": "user",
        "required": True,
        "group": None,
        "satisfied": False,
    }
    dependency[field] = value
    with pytest.raises(ValueError):
        Selection.from_json(
            {
                "skills": [{"source": "skill", "name": "skill"}],
                "resources": [],
                "dependencies": [dependency],
                "adaptations": [],
                "source_links": [],
            }
        )


@pytest.mark.parametrize("case", ["unknown-code", "bad-hash", "missing-resolution", "missing-inputs", "wrong-incoming"])
def test_update_preview_rejects_forged_conflict_or_original_evidence(tmp_path, case):
    from test_acceptance import prepared

    roots, _variant, update = prepared(tmp_path)
    path = roots.state / "previews" / update.preview_id / "preview.json"
    conflict = {
        "id": "0" * 64,
        "path": "local-review/SKILL.md",
        "code": "merge.text",
        "base_hash": None,
        "local_hash": None,
        "incoming_hash": None,
        "base_mode": None,
        "local_mode": None,
        "incoming_mode": None,
        "base_kind": None,
        "local_kind": None,
        "incoming_kind": None,
        "resolution": "Final path/hash required",
    }

    def mutate(data):
        if case == "unknown-code":
            conflict["code"] = "merge.accept-anything"
        if case == "bad-hash":
            conflict["local_hash"] = "bad"
        if case == "missing-resolution":
            conflict["resolution"] = ""
        data["conflicts"] = [conflict]
        if case == "missing-inputs":
            data["artifacts"] = []
        if case == "wrong-incoming":
            data["base_digest"] = "0" * 64

    reseal_preview(path, mutate)
    with pytest.raises(PreviewError):
        load_preview(roots.state, update.preview_id)


@pytest.mark.parametrize("case", ["relative-target", "foreign-owner", "empty-agents", "unknown-agent"])
def test_install_preview_rejects_untrusted_target_identity(tmp_path, case):
    from test_installation import install

    roots, variant = forked(tmp_path)
    prepared = install(roots, variant, tmp_path)
    path = roots.state / "previews" / prepared.preview_id / "preview.json"

    def mutate(data):
        target = data["installation_targets"][0]
        if case == "relative-target":
            target["root"] = "relative"
        if case == "foreign-owner":
            target["variant_id"] = "foreign"
        if case == "empty-agents":
            target["agents"] = []
        if case == "unknown-agent":
            target["agents"] = ["untrusted"]

    reseal_preview(path, mutate)
    with pytest.raises(PreviewError):
        load_preview(roots.state, prepared.preview_id)


def reseal_record(path, mutate):
    envelope = json.loads(path.read_bytes())
    mutate(envelope["record"])
    canonical = json.dumps(envelope["record"], sort_keys=True, separators=(",", ":")).encode()
    envelope["digest"] = hashlib.sha256(canonical).hexdigest()
    path.write_bytes(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    "case", ["schema", "id", "variant", "ledger", "missing-image", "duplicate-image", "missing-history"]
)
def test_portable_history_corruption_cannot_attest_live_content(tmp_path, case):
    from plugin_fork_io import read_status

    roots, variant = forked(tmp_path)
    import shutil

    shutil.rmtree(roots.state / "transactions")
    path = next((roots.state / "forks" / variant / "history").glob("*.json"))

    def mutate(data):
        if case == "schema":
            data["schema_version"] = 2
        elif case == "id":
            data["id"] = "other"
        elif case == "variant":
            data["variant_id"] = "other"
        elif case == "ledger":
            data["ledger"]["intent"] = ["forged"]
        elif case == "missing-image":
            data["tracking"] = []
        elif case == "duplicate-image":
            data["tracking"].append(data["tracking"][0])

    if case == "missing-history":
        path.unlink()
    else:
        reseal_record(path, mutate)
    result = read_status(roots, variant, services=Services.local())
    assert not result.allowed and not result.applied


@pytest.mark.parametrize("case", ["schema", "variant", "relative-content", "relative-target", "unknown-agent"])
def test_machine_binding_rejects_invalid_local_authority(tmp_path, case):
    from plugin_fork_io import apply_preview
    from plugin_fork_io.store import load_binding
    from test_installation import install

    roots, variant = forked(tmp_path)
    preview = install(roots, variant, tmp_path)
    assert apply_preview(roots.state, preview.preview_id, services=Services.local()).applied
    path = roots.state / "bindings" / (variant + ".json")

    def mutate(data):
        if case == "schema":
            data["schema_version"] = 2
        elif case == "variant":
            data["variant_id"] = "foreign"
        elif case == "relative-content":
            data["content_root"] = "relative"
        elif case == "relative-target":
            data["targets"][0]["root"] = "relative"
        elif case == "unknown-agent":
            data["targets"][0]["agent"] = "foreign"

    reseal_record(path, mutate)
    with pytest.raises(PreviewError):
        load_binding(roots.state, variant)


@pytest.mark.parametrize(
    "case",
    [
        "generation",
        "overlapping-ownership",
        "extra-variant",
        "blocked",
        "error-finding",
        "unowned-content",
        "missing-operation",
        "wrong-operation-mode",
    ],
)
def test_resealed_fork_preview_cannot_expand_apply_authority(tmp_path, case):
    from plugin_fork_io import apply_preview

    source = tmp_path / "source"
    write_skill(source, "review")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    preview = plan_fork(
        SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local()
    )
    path = roots.state / "previews" / preview.preview_id / "preview.json"

    def mutate(data):
        if case == "generation":
            data["expected_generations"][0][1] = 10
        elif case == "overlapping-ownership":
            data["owned_paths"].append("review/SKILL.md")
        elif case == "extra-variant":
            data["variant_ids"].append("foreign")
        elif case == "blocked":
            data["allowed"] = False
        elif case == "error-finding":
            data["findings"].append(
                {"code": "dependency.missing", "severity": "error", "path": None, "line": None, "message": "Blocked"}
            )
        elif case == "unowned-content":
            data["owned_paths"] = ["other"]
        elif case == "missing-operation":
            data["operations"] = []
        elif case == "wrong-operation-mode":
            data["operations"][-1]["mode"] = 0

    reseal_preview(path, mutate)
    result = apply_preview(roots.state, preview.preview_id, services=Services.local())
    assert not result.applied and not result.allowed
    assert not roots.content.exists()


@pytest.mark.parametrize("value", [7, "zz"])
def test_persisted_snapshot_bytes_are_validated_before_history_authority(tmp_path, value):
    from plugin_fork_io import read_status

    roots, variant = forked(tmp_path)
    path = next((roots.state / "forks" / variant / "history").glob("*.json"))

    def mutate(data):
        data["tracking"][0]["content"] = value

    reseal_record(path, mutate)
    assert not read_status(roots, variant, services=Services.local()).allowed


@pytest.mark.parametrize("record", ["ledger", "binding", "history"])
def test_tracking_authority_never_follows_replaced_record_links(tmp_path, record):
    from plugin_fork_io import apply_preview, read_status
    from plugin_fork_io.store import load_binding
    from test_installation import install

    roots, variant = forked(tmp_path)
    if record == "binding":
        preview = install(roots, variant, tmp_path)
        assert apply_preview(roots.state, preview.preview_id, services=Services.local()).applied
        path = roots.state / "bindings" / (variant + ".json")
    elif record == "ledger":
        path = roots.state / "forks" / variant / "ledger.json"
    else:
        path = next((roots.state / "forks" / variant / "history").glob("*.json"))
    outside = tmp_path / "external-record.json"
    original = path.read_bytes()
    path.rename(outside)
    path.symlink_to(outside)
    if record == "binding":
        with pytest.raises(PreviewError, match="links"):
            load_binding(roots.state, variant)
    elif record == "ledger":
        with pytest.raises(PreviewError, match="links"):
            load_ledger(roots.state, variant)
    else:
        result = read_status(roots, variant, services=Services.local())
        assert not result.allowed
    assert path.is_symlink() and outside.read_bytes() == original


def test_status_detects_a_valid_archive_that_is_not_the_accepted_base(tmp_path):
    from plugin_fork_io import read_status
    from plugin_fork_io.snapshots import capture, write_snapshot

    roots, variant = forked(tmp_path)
    other = tmp_path / "unrelated-original"
    write_skill(other, "other")
    snapshot = capture(SourceSpec(str(other), "local"), (), services=Services.local())
    path = roots.state / "forks" / variant / "base.tar.gz"
    path.unlink()
    write_snapshot(snapshot, path)
    before = (roots.content / "local-review/SKILL.md").read_bytes()
    result = read_status(roots, variant, services=Services.local())
    assert not result.allowed and "tracking.base-mismatch" in {f.code for f in result.findings}
    assert (roots.content / "local-review/SKILL.md").read_bytes() == before
