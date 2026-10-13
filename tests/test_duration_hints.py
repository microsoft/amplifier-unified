import copy
from amplifier_web.duration_hints import estimate, shape
from amplifier_web.execution import ensure_turn


def session():
    return {'selection': {'instance':'account-a','model':'model','effort':'high'},
            'workspace':'/project', 'bundle':'work', 'messages':[
                {'id':'human','inputId':'new','role':'user','inputOrigin':'ui','text':'Review this code.'}]}


def history(key, count=5):
    return [{'durationShape':copy.deepcopy(key),'phase':'completed','startedAt':100,
             'endedAt':100+seconds} for seconds in [40,50,60,70,80][:count]]


def test_range_needs_samples_and_preserves_identity():
    value=session(); key=shape(value,'new','Review this code.')
    rows=history(key)
    assert estimate(rows[:4],key) is None
    assert estimate(rows,key)=={'lowerSeconds':30,'upperSeconds':100,'sampleCount':5,
                                'basis':'recent_similar_turns','scope':'turn','version':1}
    for field in ['instance','model','effort','workspace','bundle','inputSize','attachments']:
        changed={**key,field:'different'}
        assert estimate(rows,changed) is None


def test_failed_interrupted_missing_and_unstable_history_is_not_a_prediction():
    key=shape(session(),'new','Review this code.')
    for phase in ['error','interrupted','running','cancelled']:
        assert estimate([{**r,'phase':phase} for r in history(key)],key) is None
    assert estimate([{**r,'endedAt':None} for r in history(key)],key) is None
    rows=history(key);rows[-1]['endedAt']=1000
    assert estimate(rows,key) is None
    assert estimate(history(key)+[{'phase':'running'}]*80,key) is None


def test_new_turn_uses_bounded_metadata_and_clears_an_old_hint():
    value=session();key=shape(value,'new','Review this code.')
    value['execution']={'nodes':[],'turns':[dict(row,id=str(i)) for i,row in enumerate(history(key))]}
    ensure_turn(value,'new','Review this code.')
    assert value['turnDurationHint']['sampleCount']==5
    assert value['execution']['turns'][-1]['durationShape']==key
    # Duplicate host lifecycle events do not move the prediction start time.
    hint=copy.deepcopy(value['turnDurationHint']);ensure_turn(value,'new','Review this code.')
    assert value['turnDurationHint']==hint
    ensure_turn(value,'scheduled','A machine-created turn')
    assert value['turnDurationHint'] is None


def test_machine_question_and_unselected_inputs_do_not_train():
    for field, other in [('inputOrigin','peer'),('scheduledRunId','schedule'),('questionId','question'),('steering',{'generationId':'g'})]:
        value=session();value['messages'][0][field]=other
        assert shape(value,'new','text') is None
    value=session();value['selection'].pop('instance')
    assert shape(value,'new','text') is None
