"""Tags this lane contributes to a bundle's `tags.yaml`."""

from __future__ import annotations

from okf_ext.tags import TagDefinition

CONTRIBUTED_TAGS: tuple[TagDefinition, ...] = (
    TagDefinition(name="repository", description="A page describing a git repository the workspace tracks."),
    TagDefinition(name="upstream", description="A repository the workspace reads but does not own."),
)

__all__ = ["CONTRIBUTED_TAGS"]
