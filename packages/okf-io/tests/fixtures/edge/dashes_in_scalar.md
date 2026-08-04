---
type: Policy
title: Delimiter inside a block scalar
description: |
  A horizontal rule below:
  ---
  ...and text after it. An indented `---` is content, not a delimiter.
status: stable
---

# Definition

A splitter that matches delimiters with `.strip()` closes the frontmatter at the
line inside this block scalar and truncates the document. `.rstrip()` does not.
