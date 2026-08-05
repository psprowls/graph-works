"""Fixture extension — package-local Python test."""

from mypkg.foo import foo  # noqa: F401  (imports first-party package)


def test_smoke() -> None:
    # placeholder; the fixture isn't executed, only walked for is_test=true
    assert True
