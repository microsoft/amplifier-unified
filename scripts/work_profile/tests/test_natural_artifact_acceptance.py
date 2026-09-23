"""Guard evaluator completion/routing observations without paid calls."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from natural_artifact_acceptance import completed, near_miss_checks, calls
from amplifier_web.runtime import normalize_event


def test_completion_uses_normalized_host_event_and_session():
    kind,data=normalize_event({'type':'generation.finished','input_ids':['one']},'caller')
    assert completed([{'kind':kind,**data}],'caller')
    assert not completed([{'kind':kind,**data}],'other')
    assert not completed([{'kind':'runtime.status','sessionId':'caller','status':'idle'}],'caller')


def test_near_miss_scores_answer_not_just_no_skill_call():
    assert all(near_miss_checks('csv-definition','CSV stands for comma-separated values.',{}).values())
    assert not all(near_miss_checks('csv-definition','I do not know.',{}).values())
    assert all(near_miss_checks('slide-wording','Delivery Schedule At Risk',{}).values())
    assert not all(near_miss_checks('slide-wording','Schedule Risk',{}).values())
    assert not all(near_miss_checks('slide-wording','Delivery Schedule At Risk',{'unexpected.pptx':'hash'}).values())
