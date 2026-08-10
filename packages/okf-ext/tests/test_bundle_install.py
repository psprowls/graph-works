"""The two install regimes: byte-identity for a template a package owns, and
seed-only for a file the human owns from birth."""

import os
import sys
from datetime import date

import pytest
from okf_ext.bundle import SCAFFOLD_MEMBERS, apply, plan_install, plan_scaffold

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

_FILES = {
    "_schema/Topic.schema.json": '{"type": "object"}\n',
    "_sections/Topic.yaml": "sections: []\n",
    "_config.yaml": "key: value\n",
}
_SEED_ONLY = ("_config.yaml",)


def test_install_creates_everything_into_a_bare_root(tmp_path):
    root = tmp_path / "bundle"
    plan = plan_install(root, _FILES, seed_only=_SEED_ONLY)
    assert [planned.member for planned in plan.writes] == list(_FILES)
    assert plan.ok

    result = apply(plan)
    assert result.ok
    assert result.written == tuple(_FILES)
    for member, content in _FILES.items():
        assert (root / member).read_text(encoding="utf-8") == content


def test_a_byte_identical_seed_is_skipped_not_rewritten(tmp_path):
    root = tmp_path / "bundle"
    apply(plan_install(root, _FILES, seed_only=_SEED_ONLY))
    before = (root / "_schema/Topic.schema.json").stat().st_mtime_ns

    plan = plan_install(root, _FILES, seed_only=_SEED_ONLY)
    assert plan.is_empty
    assert plan.ok
    assert {item.reason for item in plan.skipped} == {"already-present"}
    apply(plan)
    assert (root / "_schema/Topic.schema.json").stat().st_mtime_ns == before


def test_a_modified_owned_template_refuses_only_that_file(tmp_path):
    """The narrowed contract, stated as a test: one edited seed does not stop
    the other files landing, and the edit itself is not overwritten."""
    root = tmp_path / "bundle"
    (root / "_schema").mkdir(parents=True)
    (root / "_schema/Topic.schema.json").write_text('{"type": "object", "mine": true}\n', encoding="utf-8")

    plan = plan_install(root, _FILES, seed_only=_SEED_ONLY)
    assert not plan.ok
    assert [f.path for f in plan.refusals] == ["_schema/Topic.schema.json"]
    assert plan.refusals[0].kind == "foreign-content"

    result = apply(plan)
    assert result.written == ("_sections/Topic.yaml", "_config.yaml")
    assert not result.ok
    assert (root / "_schema/Topic.schema.json").read_text(encoding="utf-8") == '{"type": "object", "mine": true}\n'


def test_a_present_seed_only_file_is_skipped_whatever_its_contents(tmp_path):
    """`_repositories.yaml` is the human's from birth -- an install seeds it
    and never has an opinion about it again."""
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "_config.yaml").write_text("key: the human's own value\n", encoding="utf-8")

    plan = plan_install(root, _FILES, seed_only=_SEED_ONLY)
    assert plan.ok
    assert [item.path for item in plan.skipped] == ["_config.yaml"]
    apply(plan)
    assert (root / "_config.yaml").read_text(encoding="utf-8") == "key: the human's own value\n"


def test_declaration_members_route_to_the_declarations_dir(tmp_path):
    root = tmp_path / "bundle"
    elsewhere = tmp_path / "declarations"
    apply(plan_install(root, _FILES, seed_only=_SEED_ONLY, declarations_dir=elsewhere))

    assert (elsewhere / "_schema/Topic.schema.json").is_file()
    assert (elsewhere / "_sections/Topic.yaml").is_file()
    assert (root / "_config.yaml").is_file()
    assert not (root / "_schema").exists()


def test_two_lanes_installing_disjoint_types_coexist_in_one_declaration_dir(tmp_path):
    """`load_schemas` keys on type name, so disjoint types never collide --
    the property this whole capability is built on top of."""
    root = tmp_path / "bundle"
    apply(plan_install(root, {"_schema/Topic.schema.json": '{"type": "object"}\n'}))
    second = plan_install(root, {"_schema/Package.schema.json": '{"type": "object"}\n'})
    assert second.ok
    result = apply(second)
    assert result.written == ("_schema/Package.schema.json",)
    assert sorted(p.name for p in (root / "_schema").iterdir()) == [
        "Package.schema.json",
        "Topic.schema.json",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_an_unreadable_file_is_refused(tmp_path):
    """If a file exists but can't be read (e.g., permission denied), it's
    recorded as an unwritable failure and other files still write."""
    root = tmp_path / "bundle"
    root.mkdir()
    # Create a file with no read permissions. Use a non-seed file so it gets
    # read-tested rather than seed-checked.
    unreadable = root / "_sections/Topic.yaml"
    unreadable.parent.mkdir()
    unreadable.write_text("sections: []\n", encoding="utf-8")
    unreadable.chmod(0o000)

    try:
        plan = plan_install(root, _FILES, seed_only=_SEED_ONLY)
        assert not plan.ok
        assert len(plan.refusals) == 1
        assert plan.refusals[0].kind == "unwritable"
        assert plan.refusals[0].path == "_sections/Topic.yaml"
        # Other files still write
        result = apply(plan)
        assert result.written == ("_schema/Topic.schema.json", "_config.yaml")
    finally:
        # Restore permissions for cleanup
        unreadable.chmod(0o644)


def test_unencodable_content_for_an_absent_target_is_refused_not_raised(tmp_path):
    """A lone UTF-16 surrogate cannot be UTF-8 encoded. `apply` will eventually
    have to write this same content, so the guard has to catch it even when
    the target does not exist yet -- not only on the byte-compare path."""
    root = tmp_path / "bundle"
    plan = plan_install(root, {"bad.md": "\ud800", "good.md": "fine\n"})

    assert not plan.ok
    assert [f.path for f in plan.refusals] == ["bad.md"]
    assert plan.refusals[0].kind == "serialize-error"
    assert [w.member for w in plan.writes] == ["good.md"]

    apply(plan)
    assert (root / "good.md").read_text(encoding="utf-8") == "fine\n"
    assert not (root / "bad.md").exists()


def test_unencodable_content_for_a_present_target_is_refused_not_raised(tmp_path):
    """The reviewer's repro: an on-disk file that differs, compared against
    content that cannot even be encoded to compare. Neighbours still write."""
    root = tmp_path / "bundle"
    (root / "_schema").mkdir(parents=True)
    (root / "_schema/Topic.schema.json").write_text("different\n", encoding="utf-8")

    files = {**_FILES, "_schema/Topic.schema.json": "\ud800"}
    plan = plan_install(root, files, seed_only=_SEED_ONLY)

    assert not plan.ok
    assert [f.path for f in plan.refusals] == ["_schema/Topic.schema.json"]
    assert plan.refusals[0].kind == "serialize-error"

    result = apply(plan)
    assert result.written == ("_sections/Topic.yaml", "_config.yaml")
    assert not result.ok
    assert (root / "_schema/Topic.schema.json").read_text(encoding="utf-8") == "different\n"


def test_an_absolute_member_is_refused_not_written_outside_root(tmp_path):
    """`Path.__truediv__` discards the left operand when the right is
    absolute -- `_target` alone would have planned a write at
    `/etc/hosts_probe_TARGET`, ignoring `root` entirely."""
    root = tmp_path / "bundle"
    probe = tmp_path.parent / "hosts_probe_TARGET_outside_tmp_path"
    plan = plan_install(root, {str(probe): "malicious\n", "good.md": "fine\n"})

    assert not plan.ok
    assert [f.path for f in plan.refusals] == [str(probe)]
    assert plan.refusals[0].kind == "not-a-member"

    apply(plan)
    assert not probe.exists()
    assert (root / "good.md").read_text(encoding="utf-8") == "fine\n"


def test_a_dot_dot_climbing_member_is_refused_not_written_above_root(tmp_path):
    """`..` segments are never collapsed by `Path.__truediv__`; a member that
    climbs above `root` must not land there."""
    root = tmp_path / "sub" / "bundle"
    plan = plan_install(root, {"../escaped.md": "x\n", "good.md": "fine\n"})

    assert not plan.ok
    assert [f.path for f in plan.refusals] == ["../escaped.md"]
    assert plan.refusals[0].kind == "not-a-member"

    apply(plan)
    assert not (root.parent / "escaped.md").exists()
    assert (root / "good.md").read_text(encoding="utf-8") == "fine\n"


def test_a_non_clean_but_inside_member_is_refused_not_silently_relocated(tmp_path):
    """`./a/../_schema/X.json` resolves inside the root once normalized, but
    planning it at that normalized location would leave `InstallPlan.writes[]
    .member` disagreeing with the key the caller actually passed -- refused,
    not silently rewritten."""
    root = tmp_path / "bundle"
    plan = plan_install(root, {"./a/../_schema/X.json": '{"type": "object"}\n'})

    assert not plan.ok
    assert plan.refusals[0].path == "./a/../_schema/X.json"
    assert plan.refusals[0].kind == "not-a-member"

    apply(plan)
    assert not (root / "_schema" / "X.json").exists()


def test_an_empty_or_whitespace_member_is_refused(tmp_path):
    root = tmp_path / "bundle"
    plan = plan_install(root, {"": "x\n", "   ": "y\n", "good.md": "fine\n"})

    assert not plan.ok
    assert {f.path for f in plan.refusals} == {"", "   "}
    assert all(f.kind == "not-a-member" for f in plan.refusals)

    apply(plan)
    assert (root / "good.md").read_text(encoding="utf-8") == "fine\n"


def test_a_member_carrying_a_nul_is_refused_not_raised(tmp_path):
    """The OS refuses a NUL in a path, so leaving it to the write would turn a
    screenable member name into an uncaught `ValueError` out of `open()`."""
    root = tmp_path / "bundle"
    plan = plan_install(root, {"good\x00evil.md": "x\n", "good.md": "fine\n"})

    assert [f.path for f in plan.refusals] == ["good\x00evil.md"]
    assert plan.refusals[0].kind == "not-a-member"

    result = apply(plan)
    assert result.written == ("good.md",)
    assert not result.ok


def test_a_scaffold_member_named_by_plan_install_is_refused_and_neighbours_still_write(tmp_path):
    """The scaffold's three files are `plan_scaffold`'s to write, not any
    tier-3 package's -- a `plan_install` call naming one of them is refused
    at plan time, before any filesystem access, rather than colliding with
    the scaffold's own create-probe later."""
    root = tmp_path / "bundle"
    for index, member in enumerate(SCAFFOLD_MEMBERS):
        neighbour = f"good-{index}.md"
        plan = plan_install(root, {member: "mine\n", neighbour: "fine\n"})
        assert not plan.ok
        assert [f.path for f in plan.refusals] == [member]
        assert plan.refusals[0].kind == "not-a-member"
        assert [w.member for w in plan.writes] == [neighbour]

        result = apply(plan)
        assert result.written == (neighbour,)
        assert not (root / member).exists()
        assert (root / neighbour).is_file()


def test_an_install_first_tag_collision_no_longer_strands_the_scaffold(tmp_path):
    """The reviewer's reproduction: a tier-3 lane naming its own `_tags.yaml`
    used to be planned by `plan_install`, and because `write_all`'s
    create-probe regime is all-or-nothing, a later `_tags.yaml` collision in
    the scaffold's own pass took `index.md` and `log.md` down with it too --
    the bundle ended up with neither. `plan_install` now refuses the
    colliding member itself, before it ever reaches `write_all`, so the
    scaffold's three land normally whichever order the two run in."""
    root = tmp_path / "bundle"
    install = plan_install(root, {"_schema/A.json": "{}\n", "_tags.yaml": "version: 1\ntags: []\n"})
    assert not install.ok
    assert [f.path for f in install.refusals] == ["_tags.yaml"]
    install_result = apply(install)
    assert install_result.written == ("_schema/A.json",)

    scaffold_result = apply(plan_scaffold(root, today=date(2026, 1, 1)))
    assert scaffold_result.ok
    assert set(scaffold_result.written) == set(SCAFFOLD_MEMBERS)

    assert (root / "index.md").is_file()
    assert (root / "log.md").is_file()
    assert (root / "_tags.yaml").is_file()
