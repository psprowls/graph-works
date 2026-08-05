"""A non-markdown bundle member. A target for the bundle walk."""


def verify(receipt: dict[str, object]) -> bool:
    return bool(receipt)
