import copy
import json
from amplifier_web.conversation_navigation import query


def test_navigation_covers_turns_outside_latest_messages_without_shipping_text():
    rows=[{'id':str(i),'role':'user' if i%10==0 else 'assistant','text':'private long text '*1000} for i in range(791)]
    # The final visible page contains no user inputs.
    for row in rows[-60:]:row['role']='assistant'
    session={'id':'chat','messages':rows}
    before=copy.deepcopy(session)
    index=query(session)
    assert len(index['turns'])==74
    assert 'text' not in json.dumps(index)
    assert index['totalMessages']==791
    preview=query(session,message_id='10')
    assert len(preview['text'])==180 and len(preview['reply'])==280
    page=query(session,message_id='10',window=True)
    assert len(page['messages'])==60
    assert any(row['id']=='10' for row in page['messages'])
    assert page['before']=='0' and page['after']=='65'
    assert session==before


def test_native_index_covers_unloaded_history_and_reads_requested_window(tmp_path,monkeypatch):
    from amplifier_web import automatic_history
    root=tmp_path/'session';root.mkdir()
    (root/'transcript.jsonl').write_text(''.join(json.dumps({'role':'user' if i%2==0 else 'assistant','content':f'message {i}'})+'\n' for i in range(150)))
    monkeypatch.setattr(automatic_history,'directory',lambda session:root)
    session={'id':'native','nativeProject':'project','historyManaged':True,'messages':[]}
    index=query(session)
    assert len(index['turns'])==75
    target=index['turns'][3]['id']
    preview=query(session,message_id=target)
    assert preview['text']=='message 6' and preview['reply']=='message 7'
    page=query(session,message_id=target,window=True)
    assert len(page['messages'])==60 and page['messages'][5]['id']==target
    assert session['messages']==[]


def test_unloaded_native_message_full_text_can_be_read(tmp_path, monkeypatch):
    from amplifier_web import automatic_history
    from amplifier_web.browser_detail import read_text
    root=tmp_path/'session';root.mkdir()
    text='long saved message ' * 1000
    (root/'transcript.jsonl').write_text(json.dumps({'role':'user','content':text})+'\n')
    monkeypatch.setattr(automatic_history,'directory',lambda session:root)
    session={'id':'native','nativeProject':'project','historyManaged':True,'messages':[]}
    target=query(session)['turns'][0]['id']
    message=query(session,message_id=target,window=True)['messages'][0]
    reference=message['textDetail']
    first=read_text(session,reference)
    second=read_text(session,{**reference,'offset':first['nextOffset']})
    assert first['value']+second['value']==text
    assert session['messages']==[]
