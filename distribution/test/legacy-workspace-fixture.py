"""Create an inert migration fixture with Main's actual workspace serializers."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

legacy, destination = sys.argv[1:]
sys.path.insert(0, legacy)
from amplifier_web.workspace_starters import StarterCatalog
from amplifier_web import workspace_provisioning as setup, workspace_resources as resources

root=Path(destination);root.mkdir(mode=0o700)
metadata=root/'main-metadata';metadata.mkdir(mode=0o700)
work=root/'work';work.mkdir();ready=work/'ready';ready.mkdir();interrupted=work/'interrupted';interrupted.mkdir()
catalog=StarterCatalog(metadata,[{'value':'work','label':'Work'}])
custom=catalog.command('workspace.starters.save',{'starter':{'name':'Main custom workspace','instructions':'# Preserve this shared guidance','bundle':'work','scratch':True,'rootGit':False,'trackResources':True}},'main-starter-save')
rows=[];receipts=[]
for name,path in [('ready',ready),('interrupted',interrupted)]:
    key=str(uuid.uuid4());info=path.stat()
    receipt=setup.initialize(metadata,{'planId':key,'path':str(path),'directoryIdentity':[info.st_dev,info.st_ino],'starter':custom})
    if name=='ready':
        setup.run(metadata,key);receipt=setup.inspect(metadata,key)
        assert receipt['status']=='ready' and (path/'AGENTS.md').exists()
    else:
        receipt.update(status='running',revision=4,scaffolded=True,repositories=[{'url':'https://example.invalid/original.git','directory':'original','ref':'','status':'running'}])
        setup.receipt_path(metadata,key).write_text(json.dumps(receipt))
    wid=str(uuid.uuid4());rows.append({'id':wid,'path':str(path),'name':'Retained '+name,'setupId':key,'resourceTracking':True})
    resource=resources.command(metadata,wid,'workspace.resources.add',{'kind':'preview','resourceId':'fixture-'+name,'owner':'original chat','note':'Retain original responsibility'},'main-'+name)
    if name=='ready':resources.command(metadata,wid,'workspace.resources.status',{'id':resource['id'],'expectedRevision':1,'status':'observed_absent','evidence':'Original explicit inspection'},'main-observation')
    receipts.append(receipt)
rows.append({'id':str(uuid.uuid4()),'path':str(work/'missing'),'name':'Retained missing','archived':True})
database=root/'main.sqlite3'
with sqlite3.connect(database) as db:
    db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('INSERT INTO state VALUES(1,?)',(json.dumps({'workspaces':rows,'pinnedWorkspaceIds':[rows[1]['id'],rows[0]['id'],rows[2]['id']]}),))
files={str(p.relative_to(metadata)):hashlib.sha256(p.read_bytes()).hexdigest() for p in metadata.rglob('*.json')}
manifest=metadata/'manifest.json';manifest.write_text(json.dumps({'version':1,'files':files}))
result={'database':str(database),'databaseSha256':hashlib.sha256(database.read_bytes()).hexdigest(),'metadataManifest':str(manifest),'metadataSha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'config':{'allowedRoots':[str(work)],'defaultRoot':str(work)},'rows':rows,'setups':receipts,'customStarter':custom,'files':files,'boundary':'Actual Main StarterCatalog, setup.initialize/run and resources.command. Interrupted import is retained fixture evidence; no network/Git or inference performed.'}
(root/'seed.json').write_text(json.dumps(result));print(json.dumps({'root':str(root),'records':len(files)}))
