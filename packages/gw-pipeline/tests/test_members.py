"""Confinement of bundle-relative members."""

import pytest
from gw_pipeline.members import confined_member


@pytest.mark.parametrize("member", ["", "../outside", "/outside", "link/file"])
def test_confined_member_rejects_escape(tmp_path, member):
    root = tmp_path / "bundle"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    if member == "link/file":
        try:
            (root / "link").symlink_to(external, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"host cannot create directory symlinks: {exc}")
    with pytest.raises(ValueError, match="member outside bundle"):
        confined_member(root, member)
    assert confined_member(root, "work/a.md") == root / "work/a.md"
