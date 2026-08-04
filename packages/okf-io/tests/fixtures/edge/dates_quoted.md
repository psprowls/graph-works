---
type: Policy
title: Quoted dates
stale_after: '2026-12-31'
generated: { by: reference_agent/gemini-2.5-pro, at: '2026-06-30T14:00:00Z' }
sources:
  - id: policy
    resource: policies/revenue-recognition.md
    last_modified: '2026-06-15'
---

# Notes

The same fields quoted. The loader leaves them `str`; the view must coerce both
shapes to the same result.
