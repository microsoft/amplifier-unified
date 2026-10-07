"""Reviewed old serializer fixtures adopted while all candidate owners are closed."""
import hashlib
import json
from pathlib import Path
import sys
from amplifier_unified_operations.migrate import import_page
from amplifier_unified_operations.adopt_questions import adopt_questions
from amplifier_unified_operations.adopt_schedules import adopt_schedule

root, owner, session = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
request = json.loads((root / 'full-owner-journey.json').read_text())
review = hashlib.sha256(b'Isolated sealed old writers retired before candidate admission').hexdigest()
results = {}
for kind, name in [('questions', 'app.sqlite3'), ('schedules', 'schedules.sqlite3')]:
    source = Path(request[kind]); receipt = json.loads((source / 'receipt.json').read_text())
    db = source / name; before = db.read_bytes()
    assert hashlib.sha256(before).hexdigest() == receipt['sourceSha256']
    if kind == 'questions':
        page = import_page(db, owner, source_session=receipt['sourceSession'], session=session, kind='question')
        assert page['nextCursor'] is None and len(page['items']) == 2
        args = dict(session=session, command_id='journey-questions', records=[{'id':r['id'], 'sha256':r['sha256']} for r in page['items']], retirement_review_digest=review)
        result = adopt_questions(owner, **args)
        assert adopt_questions(owner, **args)['previouslyAdopted']
    else:
        args = dict(source_sha256=receipt['sourceSha256'], source_session=receipt['sourceSession'], session_mapping={receipt['sourceSession']:session}, schedule_id=receipt['scheduleId'], command_id='journey-schedule', retirement_review_digest=review)
        result = adopt_schedule(db, owner, **args)
        assert adopt_schedule(db, owner, **args)['previouslyAdopted']
    assert db.read_bytes() == before
    results[kind] = {'source':receipt, 'adoption':result, 'sourceUnchanged':True}
print(json.dumps(results))
