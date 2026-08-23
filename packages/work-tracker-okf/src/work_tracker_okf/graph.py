"""Iterative graph algorithms shared by work-item consumers."""

from collections.abc import Mapping, Sequence


def cycle_nodes(graph: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Return sorted nodes that participate in cycles without recursive DFS."""
    white, gray, black = 0, 1, 2
    colour = dict.fromkeys(graph, white)
    cycles: set[str] = set()
    for root in graph:
        if colour[root] != white:
            continue
        colour[root] = gray
        path = [root]
        positions = {root: 0}
        frames: list[tuple[str, int]] = [(root, 0)]
        while frames:
            node, next_child_index = frames[-1]
            children = graph.get(node, ())
            if next_child_index >= len(children):
                frames.pop()
                positions.pop(node, None)
                path.pop()
                colour[node] = black
                continue
            nxt = children[next_child_index]
            frames[-1] = (node, next_child_index + 1)
            if nxt not in colour:
                continue
            if colour[nxt] == gray:
                cycles.update(path[positions[nxt] :])
            elif colour[nxt] == white:
                colour[nxt] = gray
                positions[nxt] = len(path)
                path.append(nxt)
                frames.append((nxt, 0))
    return tuple(sorted(cycles))


__all__ = ["cycle_nodes"]
