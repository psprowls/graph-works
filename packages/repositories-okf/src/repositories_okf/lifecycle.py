"""Pure lifecycle rules: naming, lane paths, the reference page's text, and (Task 9) the restore decision. No git,
no I/O."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Literal

from okf_io import Bundle, Document

from repositories_okf.git import HeadState
from repositories_okf.lane import LANE_DIR, TYPES
from repositories_okf.pages import new_page_text
from repositories_okf.pin import Pin

#: A repository name: a lane file stem and a directory name on every platform.
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
MANAGED_TYPE = "ManagedRepository"
CheckoutDecision = Literal["checkout-present", "checkout-create", "checkout-foreign"]


def valid_name(name: str) -> bool:
    return NAME_PATTERN.match(name) is not None and ".." not in name


def default_name(url: str) -> str:
    """The URL's last path segment, lower-cased, without `.git`. Works for `scp`-style `host:org/repo.git` too."""
    tail = url.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
    return tail.removesuffix(".git").lower()


def page_path(name: str) -> str:
    return f"{LANE_DIR}/{name}.md"


def clone_path(name: str) -> str:
    return f"{LANE_DIR}/{name}/references/git"


def reference_page_text(*, name: str, url: str, track: str, pin: Pin) -> str:
    """A new `ReferenceRepository` page: the frontmatter #2's schema requires, plus the required `Summary`."""
    frontmatter: dict[str, object] = {
        "type": "ReferenceRepository",
        "title": name,
        "description": f"Reference repository {url}",
        "url": url,
        "track": track,
        "pin": pin.frontmatter(),
    }
    body = (
        f"## Summary\n\nA reference clone of `{url}`, following `{track}`. "
        f"Its checkout lives at `{clone_path(name)}/` and is rebuilt by `gw repo restore`.\n"
    )
    return new_page_text(frontmatter, body)


def managed_page_text(*, name: str, url: str, track: str, pin: Pin) -> str:
    """A managed repository page with a link to its code graph."""
    return new_page_text(
        {
            "type": MANAGED_TYPE,
            "title": name,
            "description": f"Managed repository {url}",
            "url": url,
            "track": track,
            "pin": pin.frontmatter(),
        },
        f"## Summary\n\nA repository this workspace develops. Its code graph: [{name}](/code-graph/{name}.md).\n",
    )


def scan_config_hash(path: str, ignore: Sequence[str]) -> str:
    """Hash a workspace-relative clone path and the ordered scan ignore list."""
    canonical = json.dumps({"ignore": list(ignore), "path": path}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def checkout_action(*, exists: bool, linked: bool) -> CheckoutDecision:
    """Decide whether restore can use, create, or must refuse a checkout path."""
    if not exists:
        return "checkout-create"
    return "checkout-present" if linked else "checkout-foreign"


def lane_pages(bundle: Bundle) -> dict[str, Document]:
    """Direct repository pages of either lane type, keyed by name."""
    prefix = f"{LANE_DIR}/"
    return {
        concept_id[len(prefix) :]: document
        for concept_id, document in bundle.concepts.items()
        if concept_id.startswith(prefix)
        and "/" not in concept_id[len(prefix) :]
        and document.fm_data(dates="iso").get("type") in TYPES
    }


__all__ = [
    "MANAGED_TYPE",
    "NAME_PATTERN",
    "CheckoutDecision",
    "RestoreDecision",
    "checkout_action",
    "clone_path",
    "default_name",
    "lane_pages",
    "managed_page_text",
    "page_path",
    "reference_page_text",
    "restore_action",
    "scan_config_hash",
    "valid_name",
]


RestoreDecision = Literal["present", "redetach", "clone", "no-pin", "url-mismatch", "clone-dirty"]


def restore_action(
    *,
    pin_commit: str | None,
    clone_exists: bool,
    origin: str | None,
    url: str,
    head: HeadState | None,
    clean: bool | None,
) -> RestoreDecision:
    """§4.2's table. Restore never discards: a dirty clone off its pin is refused, not reset."""
    if pin_commit is None:
        return "no-pin"
    if not clone_exists:
        return "clone"
    if origin != url:
        return "url-mismatch"
    if head is not None and head.detached and head.commit == pin_commit:
        return "present"
    if clean is not True:
        return "clone-dirty"
    return "redetach"
