"""The broken-link rule (OKF v0.2 §6.1)."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io._rules._common import member_path
from okf_io.links import absolute_form
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = ("links.broken",)


def broken(ctx: RuleContext) -> Iterable[Finding]:
    """A link or image whose target is not a bundle member.

    WARN: §6.1 and §11 are explicit that a broken link may simply
    be knowledge not yet written. okfcli reports ERROR; that is the precedent
    being rejected. ``strict=True`` still promotes it, which is the supported
    way for a CI job to be harsher than the spec.

    The message carries okfcli's one good idea: when a broken relative link
    *would* resolve from the bundle root, suggest the absolute form.
    """
    for link in ctx.links.broken:
        candidate = absolute_form(link)
        hint = ""
        if candidate is not None and ctx.bundle.has_member(candidate):
            hint = f" (did you mean `/{candidate}`?)"
        kind = "Image" if link.image else "Link"
        yield Finding(
            "links.broken",
            "warn",
            f"{kind} target `{link.raw}` is not a bundle member{hint}",
            "§6.1",
            member_path(link.source),
            link.line,
        )


RULES: tuple[Rule, ...] = (broken,)
