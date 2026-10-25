from gw_orchestrate import claims as orchestrate_claims
from gw_orchestrate import commands as orchestrate
from gw_orchestrate import dispatch, stage_advance
from work_tracker_okf.pipeline import code_phases, dispatch_phases, read_only_phases, results_phases


def test_each_constant_equals_its_accessor() -> None:
    assert read_only_phases() == orchestrate.READ_ONLY_PHASES
    assert results_phases() == stage_advance.RESULTS_PHASES
    assert code_phases() == orchestrate_claims.CODE_WRITE_PHASES
    assert code_phases() == dispatch._CODE_PHASES
    assert dispatch_phases() == orchestrate.DISPATCH_PHASES
