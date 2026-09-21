"""Evidence guards distinguish goal evaluation from a duplicate scheduled input."""
import copy
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live_schedule_acceptance import bounded_calls,same_admissions


def usage(*ids):
    return {'usage':{'calls':len(ids),'receipts':[{'id':identity,'admittedAt':1234} for identity in ids]}}


def test_goal_evaluator_is_within_explicit_two_call_bound():
    assert bounded_calls(usage('response','goal-evaluation'))
    assert not bounded_calls(usage('response','goal-evaluation','unexpected'))
    assert not bounded_calls(usage())


def test_equal_counts_do_not_hide_replaced_or_duplicate_admissions():
    before=usage('response','goal-evaluation')
    assert same_admissions(before,usage('goal-evaluation','response'))
    assert not same_admissions(before,usage('response','unexpected'))
    assert not same_admissions(before,usage('response','response'))
    missing=copy.deepcopy(before)
    missing['usage']['receipts'][0]['admittedAt']=None
    assert not bounded_calls(missing)
