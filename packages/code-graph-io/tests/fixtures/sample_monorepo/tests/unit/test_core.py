from mypkg.foo import foo
from pyutil import helper


def test_core() -> None:
    assert foo() == 1
    assert helper() == 2
