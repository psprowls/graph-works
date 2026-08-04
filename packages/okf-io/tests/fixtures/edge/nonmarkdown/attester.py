"""A non-markdown bundle member. A target for child 2's bundle walk."""


def verify(receipt: dict[str, object]) -> bool:
    return bool(receipt)
