# integration-gate-allow
# This file is a fixture inside packages/code-graph-io/tests/fixtures/sample_monorepo/,
# not a real integration test. It exists to exercise code-graph-io's discovery/scanning
# on a synthetic monorepo and never runs as a pytest integration target itself.
# Exempt from the canonical GRAPH_WIKI_RUN_INTEGRATION gate (scope decision).
from mypkg.foo import foo
from webutil import serve


def test_top() -> None:
    assert foo() == 1
    assert serve() == "ok"
