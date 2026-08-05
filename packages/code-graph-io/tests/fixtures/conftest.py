"""Prevent pytest from collecting tests inside the fixtures tree.

The sample_monorepo fixture (used by the call-order pitfall integration test)
contains files named test_*.py that are part of the fixture
data — they import first-party modules (e.g. `mypkg.foo`) that only exist
once the fixture is copied to a tmp_path and made the active project
root. Letting pytest collect them in-place fails with ModuleNotFoundError.
"""

collect_ignore_glob = ["*"]
