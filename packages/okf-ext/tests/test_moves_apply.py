"""The four regimes, the ordering, and the orphan outcome."""

from __future__ import annotations

import importlib

import ext_helpers
import pytest
from okf_ext.moves.apply import _apply_body_edits, _set_key, apply
from okf_ext.moves.model import RefEdit
from okf_ext.moves.plan import plan_move, plan_move_dir, plan_move_many, plan_repair
from okf_io import Document, build_link_graph, load_bundle

# `importlib.import_module`, not `from okf_ext.moves import apply as
# apply_module` or `import okf_ext.moves.apply as apply_module`: the module
# and its sole public function share the name `apply`, and `okf_ext.moves`'s
# `from okf_ext.moves.apply import apply` rebinds the `apply` *attribute* on
# the `okf_ext.moves` package object to the function once the package is
# imported. Both of those other spellings resolve through that same
# attribute in the end (`import a.b.c as x` still does `getattr` chains under
# the hood), so both would silently hand back the function here instead of
# the module. `importlib.import_module` reads `sys.modules` directly and is
# the one spelling immune to the shadowing.
apply_module = importlib.import_module("okf_ext.moves.apply")


@pytest.fixture
def live(tmp_path):
    root = ext_helpers.linked_copy(tmp_path)
    return root, load_bundle(root)


# --- the two ValueErrors ---


def test_a_plan_from_another_bundle_raises(live, tmp_path):
    root, bundle = live
    other = load_bundle(ext_helpers.linked_copy(tmp_path / "second"))
    plan = plan_move(other, "concepts/beta.md", "pages/beta.md")
    with pytest.raises(ValueError, match="different bundle"):
        apply(bundle, plan)
    assert (root / "concepts" / "beta.md").exists()
    assert not (root / "pages").exists()


def test_a_non_ok_plan_raises(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/alpha.md", "concepts/beta.md")
    assert not plan.ok
    with pytest.raises(ValueError, match="refusal"):
        apply(bundle, plan)
    assert (root / "concepts" / "alpha.md").exists()


# --- regime 1: content build, all-or-nothing ---


def test_a_stale_body_aborts_the_whole_batch(live):
    """The deliberate divergence from `tags.apply`: a move is a multi-file
    transaction, so a partially-applied move costs more than a partial rename."""
    root, bundle = live
    before = ext_helpers.snapshot(root)
    plan = plan_move(bundle, "concepts/alpha.md", "pages/alpha.md")
    (root / "concepts" / "multi.md").write_text("---\ntitle: Multi\n---\n\n# Changed\n", encoding="utf-8")
    reloaded = load_bundle(root)
    result = apply(reloaded, plan)
    assert not result.ok
    assert {f.kind for f in result.failed} == {"stale"}
    assert {f.path for f in result.failed} == {"concepts/multi.md"}
    assert result.moved == () and result.written == () and result.pruned == ()
    assert (root / "concepts" / "alpha.md").exists(), "nothing may be written when the batch aborts"
    assert not (root / "pages").exists()
    after = ext_helpers.snapshot(root)
    changed = {name for name in before if before[name] != after.get(name)}
    assert changed == {"concepts/multi.md"}, "only the file the test itself edited may differ"


def test_a_stale_frontmatter_value_aborts_the_whole_batch(live):
    """The frontmatter staleness check is a re-read of the key, not a digest:
    a §6.2 edit is position-free, so a body digest would never notice it."""
    root, bundle = live
    plan = plan_move(bundle, "assets/diagram.png", "img/diagram.png")
    beta = root / "concepts" / "beta.md"
    beta.write_text(
        beta.read_text(encoding="utf-8").replace(
            "resource: ../assets/diagram.png\nsources:", "resource: ../assets/other.png\nsources:", 1
        ),
        encoding="utf-8",
    )
    reloaded = load_bundle(root)
    result = apply(reloaded, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("concepts/beta.md", "stale")]
    assert "resource" in result.failed[0].error
    assert (root / "assets" / "diagram.png").exists()
    assert not (root / "img").exists()


def test_a_serialize_failure_aborts_the_whole_batch(live, monkeypatch):
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")

    def boom(self):
        raise ValueError("no render for you")

    monkeypatch.setattr(Document, "serialize", boom)
    result = apply(bundle, plan)
    assert not result.ok
    assert {f.kind for f in result.failed} == {"serialize-error"}
    assert all("no render for you" in f.error for f in result.failed)
    assert result.moved == () and result.written == ()
    assert not (root / "pages").exists()
    assert (root / "concepts" / "beta.md").exists()


# --- the happy path ---


def test_a_move_lands_and_every_inbound_reference_repairs(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")
    result = apply(bundle, plan)
    assert result.ok, result.failed
    assert ("concepts/beta.md", "pages/beta.md") in result.moved
    assert (root / "pages" / "beta.md").exists()
    assert not (root / "concepts" / "beta.md").exists()

    reloaded = load_bundle(root)
    assert "pages/beta" in reloaded.concepts
    assert not build_link_graph(reloaded).broken


def test_the_moved_member_keeps_its_bytes_apart_from_its_own_rebase(live):
    """A moved member is re-serialized from a scratch clone, so this is the
    check that the clone did not disturb anything the plan never named."""
    root, bundle = live
    before = (root / "concepts" / "beta.md").read_bytes()
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")
    assert apply(bundle, plan).ok
    after = (root / "pages" / "beta.md").read_bytes()
    # Only the one outbound reference whose *resolved* target changed relative
    # position is rewritten. `concepts/` and `pages/` sit at the same depth, so
    # `../assets/diagram.png` rebases to itself and its two §6.2 keys and its
    # body image are all left byte-identical -- which is the point: the scratch
    # clone must not perturb a value the rebase computed as unchanged.
    assert after == before.replace(b"[alpha](./alpha.md)", b"[alpha](../concepts/alpha.md)")
    assert after.count(b"../assets/diagram.png") == 2


def test_a_moved_member_with_no_references_is_byte_identical(live):
    """`_build` renders every moved markdown member through `serialize()`, even
    one with nothing to rewrite. Byte fidelity says that must be a no-op."""
    root, bundle = live
    before = (root / "concepts" / "café.md").read_bytes()
    plan = plan_move(bundle, "concepts/café.md", "pages/café.md")
    result = apply(bundle, plan)
    assert result.ok, result.failed
    assert (root / "pages" / "café.md").read_bytes() == before


def test_untouched_members_are_byte_identical(live):
    root, bundle = live
    before = ext_helpers.snapshot(root)
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")
    assert apply(bundle, plan).ok
    after = ext_helpers.snapshot(root)
    # `spaced name.md` and `café.md` link nowhere; `notes/gamma.md` links only
    # at alpha. None of the three may have been rewritten.
    for name in ("concepts/café.md", "concepts/spaced name.md", "notes/gamma.md", "log.md"):
        assert after[name] == before[name], f"{name} was rewritten and should not have been"


def test_several_edits_on_one_line_all_land(live):
    """Body edits are spliced descending by `(line, column)`, so an earlier
    column stays valid while a later one on the same line is rewritten."""
    root, bundle = live
    plan = plan_move(bundle, "concepts/alpha.md", "pages/alpha.md")
    assert apply(bundle, plan).ok
    multi = (root / "concepts" / "multi.md").read_text(encoding="utf-8")
    assert "[a](../pages/alpha.md), [b](./beta.md), [c](../pages/alpha.md)" in multi
    assert "[d](../notes/gamma.md)" in multi, "the unrelated destination is untouched"


def test_an_encoded_and_a_bracketed_destination_survive_a_directory_move(live):
    root, bundle = live
    plan = plan_move_dir(bundle, "concepts", "pages")
    assert apply(bundle, plan).ok
    encoded = (root / "pages" / "encoded.md").read_text(encoding="utf-8")
    assert "[café](./caf%C3%A9.md)" in encoded
    assert "[spaced](<./spaced name.md>)" in encoded


def test_an_asset_move_renames_directly(live):
    root, bundle = live
    before = (root / "assets" / "diagram.png").read_bytes()
    plan = plan_move(bundle, "assets/diagram.png", "img/diagram.png")
    result = apply(bundle, plan)
    assert result.ok, result.failed
    assert result.moved == (("assets/diagram.png", "img/diagram.png"),)
    assert (root / "img" / "diagram.png").read_bytes() == before
    assert not (root / "assets" / "diagram.png").exists()
    beta = (root / "concepts" / "beta.md").read_text(encoding="utf-8")
    assert beta.count("../img/diagram.png") == 2, "both §6.2 keys repaired"
    assert "![diagram](../img/diagram.png)" in (root / "concepts" / "alpha.md").read_text(encoding="utf-8")


def test_an_asset_replace_failure_is_reported(live):
    """`Path.replace` is not staged, so its own failure needs its own kind."""
    root, bundle = live
    plan = plan_move(bundle, "assets/diagram.png", "img/diagram.png")
    (root / "img").mkdir()
    (root / "img" / "diagram.png").mkdir()  # a directory where the file must land
    result = apply(bundle, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("img/diagram.png", "asset-replace-error")]
    assert result.moved == () and result.written == ()
    assert (root / "assets" / "diagram.png").is_file(), "the source is untouched when the rename fails"
    assert "../assets/diagram.png" in (root / "concepts" / "beta.md").read_text(encoding="utf-8")


def test_a_failed_markdown_commit_does_not_consume_an_asset_source(tmp_path, monkeypatch):
    """The markdown commit loop and the asset rename loop are separate steps,
    and **100% branch coverage does not catch their interaction**: "a markdown
    commit fails" and "an asset rename succeeds" are each covered on their own,
    never together, so the missing gate between them read as fully tested. This
    is that pairing.

    A mixed batch is the flagship case -- `plan_move_dir` over a directory
    holding markdown and images alike -- so a transient I/O failure on one
    markdown file must not go on to consume every asset in the batch. An asset
    rename has no orphan copy to fall back on, which is exactly why it must not
    be attempted once the run is already going to stop before `write_all`.
    """
    root = tmp_path / "mixed"
    bundle = ext_helpers.write_bundle(
        root,
        {
            "one.png": "\x89PNG-ish\n",
            "hub.md": "---\ntitle: Hub\n---\n\n# Hub\n",
            "citer.md": "---\ntitle: C\n---\n\n# C\n\n![a](./one.png) and [h](./hub.md)\n",
        },
    )
    plan = plan_move_many(bundle, {"one.png": "img/one.png", "hub.md": "moved/hub.md"})
    assert plan.ok, plan.refusals

    from pathlib import Path as _Path

    original = _Path.replace

    def flaky(self, target):
        if _Path(target).name == "hub.md":
            raise OSError("rename refused")
        return original(self, target)

    monkeypatch.setattr(_Path, "replace", flaky)
    result = apply(bundle, plan)

    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("moved/hub.md", "commit-error")]
    assert result.moved == (), "no asset may be renamed once a destination has already failed"
    assert result.written == ()
    assert (root / "one.png").is_file(), "the asset source must survive an unrelated markdown failure"
    assert not (root / "img" / "one.png").exists()
    assert (root / "hub.md").is_file()
    assert not build_link_graph(load_bundle(root)).broken, "nothing dangles"


def test_an_asset_that_landed_before_a_later_asset_fails_leaves_its_referrers_dangling(tmp_path, monkeypatch):
    """**The one documented hole in the no-dangle invariant.** An asset rename
    is a single `Path.replace`, so it consumes its source the instant it
    commits -- unlike a markdown source, whose removal is deferred to regime 4
    and gated on `source-kept`. A *later asset* rename failing therefore stops
    the run with the first asset already gone and its referrers not yet
    repaired.

    Still reachable after the gate added for
    `test_a_failed_markdown_commit_does_not_consume_an_asset_source`: `problems`
    is still empty when the first asset is attempted, so the accepted window is
    preserved exactly and only the wider markdown-failure case is closed.
    Inherent to the direct rename; pinned here so it cannot drift into being
    undocumented.
    """
    root = tmp_path / "assets"
    bundle = ext_helpers.write_bundle(
        root,
        {
            "one.png": "\x89PNG-ish\n",
            "two.png": "\x89PNG-ish too\n",
            "citer.md": "---\ntitle: C\n---\n\n# C\n\n![a](./one.png) and ![b](./two.png)\n",
        },
    )
    plan = plan_move_many(bundle, {"one.png": "img/one.png", "two.png": "img/two.png"})
    assert plan.ok, plan.refusals

    from pathlib import Path as _Path

    original = _Path.replace
    calls: list[str] = []

    def flaky(self, target):
        calls.append(_Path(target).name)
        if len(calls) == 2:
            raise OSError("rename refused")
        return original(self, target)

    monkeypatch.setattr(_Path, "replace", flaky)
    result = apply(bundle, plan)
    assert not result.ok
    assert [f.kind for f in result.failed] == ["asset-replace-error"]
    assert result.moved == (("one.png", "img/one.png"),)
    assert not (root / "one.png").exists(), "the rename already consumed it -- no orphan copy to fall back on"
    assert result.written == (), "the run stopped before any referrer was repaired"
    dangling = [link.raw for link in build_link_graph(load_bundle(root)).broken]
    assert dangling == ["./one.png"], "documented: `plan_repair` on {'one.png': 'img/one.png'} is the recovery"


def test_mkdir_creates_the_destination_parents(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "a/b/c/beta.md")
    assert apply(bundle, plan).ok
    assert (root / "a" / "b" / "c" / "beta.md").exists()


def test_a_mkdir_failure_reports_mkdir_error_and_touches_no_live_file(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")
    (root / "pages").write_bytes(b"a file sitting where the directory must go")
    result = apply(bundle, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("pages/beta.md", "mkdir-error")]
    assert result.moved == () and result.written == () and result.pruned == ()
    assert (root / "concepts" / "beta.md").exists()
    assert (root / "pages").read_bytes() == b"a file sitting where the directory must go"
    assert "[beta](./beta.md)" in (root / "concepts" / "alpha.md").read_text(encoding="utf-8")


def test_an_emptied_source_directory_is_pruned(live):
    """`notes/` holds `gamma.md` directly and `sub/{x,keep}.md` a level
    deeper, so moving the whole directory empties both `notes/sub` and
    `notes` -- deepest first, matching `_prune`'s own ordering."""
    root, bundle = live
    plan = plan_move_dir(bundle, "notes", "moved-notes")
    result = apply(bundle, plan)
    assert result.ok, result.failed
    assert result.pruned == ("notes/sub", "notes")
    assert not (root / "notes").exists()
    assert (root / "moved-notes" / "gamma.md").exists()
    assert (root / "moved-notes" / "sub" / "x.md").exists()
    assert (root / "moved-notes" / "sub" / "keep.md").exists()


def test_a_source_directory_that_did_not_empty_is_kept(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")
    result = apply(bundle, plan)
    assert result.ok, result.failed
    assert result.pruned == ()
    assert (root / "concepts").is_dir()


def test_nested_source_directories_prune_deepest_first(tmp_path):
    root = tmp_path / "nested"
    bundle = ext_helpers.write_bundle(
        root,
        {
            "a/b/inner.md": "---\ntitle: Inner\n---\n\n# Inner\n",
            "a/b/c/deeper.md": "---\ntitle: Deeper\n---\n\n# Deeper\n",
            "hub.md": "# Hub\n\n[i](a/b/inner.md) and [d](a/b/c/deeper.md).\n",
        },
    )
    plan = plan_move_many(bundle, {"a/b/inner.md": "flat/inner.md", "a/b/c/deeper.md": "flat/deeper.md"})
    result = apply(bundle, plan)
    assert result.ok, result.failed
    assert result.pruned == ("a/b/c", "a/b"), "deepest first: `a/b` only empties once `a/b/c` is gone"
    # `a` held no source of its own, so it is not a candidate -- documented in
    # `_prune`, and asserted here so the limitation cannot drift unnoticed.
    assert (root / "a").is_dir() and not any((root / "a").iterdir())
    assert not build_link_graph(load_bundle(root)).broken


def test_a_directory_move_repairs_every_reference(live):
    root, bundle = live
    plan = plan_move_dir(bundle, "concepts", "pages")
    result = apply(bundle, plan)
    assert result.ok, result.failed
    reloaded = load_bundle(root)
    assert not build_link_graph(reloaded).broken
    assert result.pruned == ("concepts",)


def test_a_reserved_file_keeps_its_hand_authored_text(live):
    """Spec §7.2: repairing the entry in place leaves `update_index()` with
    nothing to do, which is the correct outcome — okf-io owns *which* entries
    appear, the human owns *what they say*."""
    root, bundle = live
    plan = plan_move(bundle, "concepts/alpha.md", "pages/alpha.md")
    assert apply(bundle, plan).ok
    index = (root / "index.md").read_text(encoding="utf-8")
    assert "the entry point, and the one with every link form." in index
    assert "pages/alpha.md" in index
    assert "concepts/alpha.md" not in index
    assert "[Beta](concepts/beta.md)" in index, "an unrelated entry is byte-untouched"
    assert "[alpha](pages/alpha.md)" in (root / "log.md").read_text(encoding="utf-8")


def test_the_crlf_member_keeps_its_endings(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/alpha.md", "pages/alpha.md")
    assert apply(bundle, plan).ok
    raw = (root / "concepts" / "crlf.md").read_bytes()
    assert b"[alpha](../pages/alpha.md)" in raw, "the reference was actually repaired"
    assert b"\r\n" in raw and raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_a_crlf_member_that_moves_keeps_its_endings(live):
    root, bundle = live
    before = (root / "concepts" / "crlf.md").read_bytes()
    plan = plan_move_dir(bundle, "concepts", "pages")
    assert apply(bundle, plan).ok
    raw = (root / "pages" / "crlf.md").read_bytes()
    assert raw == before, "both endpoints moved together, so the relative form is unchanged"
    assert b"\r\n" in raw and raw.replace(b"\r\n", b"").count(b"\n") == 0


# --- regime 4: the orphan outcome ---


def test_a_referrer_that_does_not_land_keeps_the_source(live, monkeypatch):
    """The invariant's price, reported rather than silent: an orphan copy
    survives at the old path and `source-kept` says so."""
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")

    def refuse_everything(pending, *, failed=(), skipped=()):
        from okf_ext.writing import ApplyResult, WriteFailure

        return ApplyResult(
            written=(),
            failed=tuple(WriteFailure(path=item.member, kind="commit-error", error="boom") for item in pending),
            skipped=(),
        )

    monkeypatch.setattr(apply_module, "write_all", refuse_everything)
    result = apply(bundle, plan)
    assert not result.ok
    assert "source-kept" in {f.kind for f in result.failed}
    assert (root / "concepts" / "beta.md").exists(), "the orphan must survive so nothing dangles"
    assert (root / "pages" / "beta.md").exists()
    assert result.pruned == ()
    kept = next(f for f in result.failed if f.kind == "source-kept")
    assert kept.path == "concepts/beta.md"
    assert "plan_repair" in kept.error


def test_a_frontmatter_only_referrer_that_does_not_land_keeps_the_source(live, monkeypatch):
    """`referrers_of` is keyed by `RefEdit.target`, which a frontmatter edit
    carries too — so a dangling `resource:` keeps the source exactly as a
    dangling body link does."""
    root, bundle = live
    plan = plan_move(bundle, "assets/diagram.png", "img/diagram.png")
    # `concepts/beta.md` is the only member whose reference lives in §6.2 keys
    # *and* in the body; `concepts/alpha.md` refers to the asset from its body
    # alone. Refusing only beta proves the frontmatter target is the key.
    real = apply_module.write_all

    def refuse_beta(pending, *, failed=(), skipped=()):
        from okf_ext.writing import ApplyResult, WriteFailure

        keep = [item for item in pending if item.member != "concepts/beta.md"]
        outcome = real(keep, failed=failed, skipped=skipped)
        return ApplyResult(
            written=outcome.written,
            failed=(*outcome.failed, WriteFailure(path="concepts/beta.md", kind="commit-error", error="boom")),
            skipped=outcome.skipped,
        )

    monkeypatch.setattr(apply_module, "write_all", refuse_beta)
    result = apply(bundle, plan)
    assert not result.ok
    # An asset has no deferred removal to decline: the rename already consumed
    # its source. What must hold is that the failure is reported and the
    # landed referrer is the only one rewritten.
    assert {f.kind for f in result.failed} == {"commit-error"}
    assert "concepts/alpha.md" in result.written
    assert "../assets/diagram.png" in (root / "concepts" / "beta.md").read_text(encoding="utf-8")


def test_the_source_is_kept_when_only_a_frontmatter_referrer_fails(tmp_path, monkeypatch):
    """The same question for a markdown source, where the removal *is*
    deferred: a frontmatter referrer that did not land keeps the source."""
    root = tmp_path / "fm"
    bundle = ext_helpers.write_bundle(
        root,
        {
            "target.md": "---\ntitle: Target\n---\n\n# Target\n",
            "citer.md": "---\ntitle: Citer\nresource: ./target.md\n---\n\n# Citer\n\nNo body link.\n",
        },
    )
    plan = plan_move(bundle, "target.md", "moved/target.md")
    assert plan.ok, plan.refusals
    assert [(e.member, e.where) for e in plan.edits] == [("citer.md", "frontmatter")]

    def refuse_everything(pending, *, failed=(), skipped=()):
        from okf_ext.writing import ApplyResult, WriteFailure

        return ApplyResult(
            written=(),
            failed=tuple(WriteFailure(path=item.member, kind="commit-error", error="boom") for item in pending),
            skipped=(),
        )

    monkeypatch.setattr(apply_module, "write_all", refuse_everything)
    result = apply(bundle, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [
        ("citer.md", "commit-error"),
        ("target.md", "source-kept"),
    ]
    assert (root / "target.md").exists(), "a dangling `resource:` is as broken as a dangling link"
    assert (root / "moved" / "target.md").exists()


def test_an_unlink_failure_is_reported_as_unlink_error(live, monkeypatch):
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")

    from pathlib import Path as _Path

    original = _Path.unlink

    def refuse(self, missing_ok=False):
        if self.name == "beta.md" and self.parent.name == "concepts":
            raise OSError("device is on fire")
        return original(self, missing_ok=missing_ok)

    monkeypatch.setattr(_Path, "unlink", refuse)
    result = apply(bundle, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("concepts/beta.md", "unlink-error")]
    assert "device is on fire" in result.failed[0].error
    assert (root / "concepts" / "beta.md").exists() and (root / "pages" / "beta.md").exists()
    assert result.pruned == ()


def test_destinations_commit_before_referrers(live, monkeypatch):
    """The whole basis of the no-dangling-reference invariant."""
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")
    seen: list[bool] = []
    real = apply_module.write_all

    def record(pending, *, failed=(), skipped=()):
        seen.append((root / "pages" / "beta.md").exists())
        return real(pending, failed=failed, skipped=skipped)

    monkeypatch.setattr(apply_module, "write_all", record)
    assert apply(bundle, plan).ok
    assert seen == [True], "the destination must already exist when referrers are written"


def test_an_unwritable_referrer_aborts_before_anything_commits(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/beta.md", "pages/beta.md")
    target = root / "concepts" / "alpha.md"
    target.chmod(0o444)
    try:
        # Decide the skip from the filesystem itself, never from `apply`'s own
        # result: a bug that let `apply` succeed here must fail the test, not
        # silently skip it.
        try:
            with target.open("r+b"):
                permissive = True
        except OSError:
            permissive = False
        if permissive:
            pytest.skip("filesystem allows r+b on a read-only file (running as root?)")
        result = apply(bundle, plan)
    finally:
        target.chmod(0o644)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("concepts/alpha.md", "unwritable")]
    assert not (root / "pages" / "beta.md").exists()
    assert (root / "concepts" / "beta.md").exists()
    assert result.moved == () and result.written == ()


def test_no_temp_file_survives_a_successful_apply(live):
    root, bundle = live
    plan = plan_move_dir(bundle, "concepts", "pages")
    assert apply(bundle, plan).ok
    leftovers = [p.as_posix() for p in root.rglob("*.tmp")]
    assert leftovers == []


def test_no_temp_file_survives_an_aborted_commit(live, monkeypatch):
    """A failed `tmp.replace(live)` must still take its temp file with it."""
    root, bundle = live
    plan = plan_move_dir(bundle, "concepts", "pages")

    from pathlib import Path as _Path

    original = _Path.replace
    calls: list[str] = []

    def flaky(self, target):
        calls.append(str(target))
        if len(calls) == 2:
            raise OSError("rename refused")
        return original(self, target)

    monkeypatch.setattr(_Path, "replace", flaky)
    result = apply(bundle, plan)
    assert not result.ok
    assert {f.kind for f in result.failed} == {"commit-error"}
    assert [p.as_posix() for p in root.rglob("*.tmp")] == []
    assert result.written == (), "no referrer may be written once a destination failed"
    assert (root / "concepts" / "alpha.md").exists(), "every source is still in place"


# --- plan_repair ---


def test_plan_repair_writes_referrers_and_relocates_nothing(live):
    root, _bundle = live  # the plan is built against the *reloaded* bundle below
    (root / "pages").mkdir()
    (root / "concepts" / "beta.md").rename(root / "pages" / "beta.md")
    reloaded = load_bundle(root)
    plan = plan_repair(reloaded, {"concepts/beta.md": "pages/beta.md"})
    assert plan.ok, plan.refusals
    result = apply(reloaded, plan)
    assert result.ok, result.failed
    assert result.moved == ()
    assert result.pruned == ()
    assert (root / "pages" / "beta.md").exists()

    alpha = (root / "concepts" / "alpha.md").read_text(encoding="utf-8")
    assert "[beta](../pages/beta.md)" in alpha
    assert "[beta again](/pages/beta.md)" in alpha
    assert "[Beta](pages/beta.md)" in (root / "index.md").read_text(encoding="utf-8")

    # `plan_repair` is **inbound only** by documented design (see its
    # docstring): a moved member's own outbound references are not rebased,
    # because after a partial `apply` the destination content was already
    # written with its rebasing. So exactly one link stays broken here, and
    # asserting a clean graph would assert behaviour this function does not
    # have.
    broken = build_link_graph(load_bundle(root)).broken
    assert [(link.source, link.raw) for link in broken] == [("pages/beta", "./alpha.md")]


def test_a_root_level_source_has_no_directory_to_prune(tmp_path):
    root = tmp_path / "flat"
    bundle = ext_helpers.write_bundle(
        root,
        {
            "hub.md": "---\ntitle: Hub\n---\n\n# Hub\n",
            "citer.md": "---\ntitle: Citer\n---\n\n# Citer\n\n[hub](./hub.md)\n",
        },
    )
    result = apply(bundle, plan_move(bundle, "hub.md", "deep/hub.md"))
    assert result.ok, result.failed
    assert result.pruned == (), "the bundle root is never a prune candidate"
    assert root.is_dir() and (root / "citer.md").exists()
    # The author's leading `./` rides through: `_rewrite_path` preserves it.
    assert "[hub](./deep/hub.md)" in (root / "citer.md").read_text(encoding="utf-8")


def test_a_prune_failure_is_swallowed_and_is_not_a_move_failure(live, monkeypatch):
    root, bundle = live
    plan = plan_move_dir(bundle, "notes", "moved-notes")

    from pathlib import Path as _Path

    def refuse(self):
        raise OSError("directory is busy")

    monkeypatch.setattr(_Path, "rmdir", refuse)
    result = apply(bundle, plan)
    assert result.ok, "a prune has no failure path -- the move already succeeded"
    assert result.pruned == ()
    assert (root / "notes").is_dir(), "the directory survives, empty"
    assert (root / "moved-notes" / "gamma.md").exists()


def test_a_referrer_deleted_after_planning_is_reported_as_not_a_member(live):
    root, bundle = live
    plan = plan_move(bundle, "concepts/alpha.md", "pages/alpha.md")
    (root / "concepts" / "multi.md").unlink()
    reloaded = load_bundle(root)
    result = apply(reloaded, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("concepts/multi.md", "not-a-member")]
    assert result.moved == () and result.written == ()
    assert (root / "concepts" / "alpha.md").exists()
    assert not (root / "pages").exists()


def test_a_staging_failure_aborts_the_batch_and_leaves_no_temp_file(live, monkeypatch):
    root, bundle = live
    plan = plan_move_dir(bundle, "concepts", "pages")

    from pathlib import Path as _Path

    original = _Path.write_bytes
    seen: list[str] = []

    def flaky(self, data):
        seen.append(self.name)
        if len(seen) == 3:
            self.write_text("half a file", encoding="utf-8")  # a truncated temp, as a real failure leaves
            raise OSError("no space left on device")
        return original(self, data)

    monkeypatch.setattr(_Path, "write_bytes", flaky)
    result = apply(bundle, plan)
    assert not result.ok
    assert {f.kind for f in result.failed} == {"stage-error"}
    assert result.moved == () and result.written == () and result.pruned == ()
    assert [p.as_posix() for p in root.rglob("*.tmp")] == [], "every staged temp is removed, the failed one included"
    assert sorted(p.name for p in (root / "pages").iterdir()) == [], "no live destination file was created"
    assert (root / "concepts" / "alpha.md").exists()


def test_a_span_that_moved_under_an_unfingerprinted_plan_aborts_the_batch(live):
    """The digest normally catches a changed body first. Strip it and the
    `(line, column, old)` span check is what stands between a stale plan and a
    corrupted file -- so it needs a test that reaches it."""
    import dataclasses

    root, bundle = live
    plan = plan_move(bundle, "concepts/alpha.md", "pages/alpha.md")
    blind = dataclasses.replace(plan, digests={})
    multi = root / "concepts" / "multi.md"
    multi.write_text(multi.read_text(encoding="utf-8").replace("[a](./alpha.md)", "[a](./ALPHA.md)"), encoding="utf-8")
    reloaded = load_bundle(root)
    result = apply(reloaded, blind)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("concepts/multi.md", "stale")]
    assert "no longer holds the text it claims" in result.failed[0].error
    assert result.moved == () and result.written == ()
    assert not (root / "pages").exists()


# --- the two staleness helpers, whose defensive branches the digest hides ---


def test_apply_body_edits_declines_a_span_past_the_end():
    edit = RefEdit(member="a.md", where="body", target="b.md", old="x", new="y", line=99, column=0)
    assert _apply_body_edits("one line\n", [edit]) is None


def test_apply_body_edits_declines_a_span_that_moved():
    edit = RefEdit(member="a.md", where="body", target="b.md", old="xx", new="y", line=1, column=0)
    assert _apply_body_edits("one line\n", [edit]) is None


def test_apply_body_edits_declines_a_positionless_edit():
    edit = RefEdit(member="a.md", where="body", target="b.md", old="x", new="y")
    assert _apply_body_edits("one line\n", [edit]) is None


def test_set_key_walks_mappings_and_sequences():
    raw = {"sources": [{"resource": "old"}], "executor": {"resource": "old"}}
    assert _set_key(raw, "sources.0.resource", "old", "new") is True
    assert _set_key(raw, "executor.resource", "old", "new") is True
    assert raw == {"sources": [{"resource": "new"}], "executor": {"resource": "new"}}


def test_set_key_rewrites_a_scalar_list_element():
    raw = {"items": ["old"]}
    assert _set_key(raw, "items.0", "old", "new") is True
    assert raw == {"items": ["new"]}


@pytest.mark.parametrize(
    "raw,key",
    [
        ({"a": "old"}, "a.b"),  # a scalar where a mapping was expected
        ({}, "missing"),  # absent leaf
        ({"a": {}}, "a.b"),  # absent nested leaf
        ({"a": "old"}, "b.0.c"),  # absent intermediate mapping key
        ({"a": "scalar"}, "a.0.c"),  # an index into a string
        ({"a": []}, "a.0.c"),  # an index past the end
        ({"a": "old"}, "a.0"),  # a leaf index into a non-sequence
        ({"a": []}, "a.0"),  # a leaf index past the end
        ({"a": ["other"]}, "a.0"),  # a leaf index holding something else
        ({"a": "other"}, "a"),  # a leaf holding something else
    ],
)
def test_set_key_declines_every_shape_that_no_longer_matches(raw, key):
    snapshot = repr(raw)
    assert _set_key(raw, key, "old", "new") is False
    assert repr(raw) == snapshot, "a declined rewrite must leave the map untouched"
