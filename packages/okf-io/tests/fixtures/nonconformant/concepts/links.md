---
type: Metric
title: Links
description: Every link shape the resolver must get right.
---

# Definition

A link that resolves: [ok](./no-type.md).

A link that does not: [gone](./nowhere.md).

A link that would resolve from the root: [sub](sub/index.md).

A link that escapes: [out](../../outside.md).

An external one, never reported: [ext](https://example.com/x).

An image that resolves: ![d](/assets/diagram.svg).

An image that does not: ![missing](/assets/none.png).

```markdown
Not an edge: [fenced](/nope.md)
```
