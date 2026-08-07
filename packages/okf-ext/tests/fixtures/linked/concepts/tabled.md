---
title: Tabled
type: concept
---

# Tabled

A link inside a markdown table row is prose, not code, and repairs like any other:

| Page | Link |
| --- | --- |
| Alpha | [alpha](./alpha.md) |
| Beta | [beta](./beta.md) |

A bare path in a cell is *not* a reference and is never rewritten: `alpha.md`.
