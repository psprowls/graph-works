"""Wiki application slice over resolved kernel primitives.

The independent verticals are scan, ingest, query, lint_drift, proposals,
wiki_page, code_read, repositories, agent_config and util. Shared role and
agent-loop support lives in agent_substrate; prompt fragments live in prompts.
Import callable APIs from their defining modules.
"""
