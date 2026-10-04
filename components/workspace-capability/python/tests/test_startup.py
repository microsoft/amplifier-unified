import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from amplifier_unified_workspaces.owner import Owner

KIND="workspace"
pytestmark=pytest.mark.asyncio
TABLES=["meta","registrations","plans","commands"] if KIND=="workspace" else ["commands"]

class Fixture:
    def __init__(self, root):
        self.root=root
        self.projects=root/"projects";self.projects.mkdir()
        self.state=root/"owner"
        self.calls=0
        self.main=self.state/("workspaces.sqlite" if KIND=="workspace" else "commands.sqlite3")
        self.config={"stateDirectory":str(self.state),"allowedRoots":[str(self.projects)],"defaultRoot":str(self.projects)} if KIND=="workspace" else {"dataDir":str(self.state)}
        self.args={"operation":"locations.create","args":{"path":str(self.projects),"name":"inert"},"commandId":"original","clientId":"fixture"} if KIND=="workspace" else {"operation":"coordination.followup","args":{"sessionId":"ahp-session:/fixture","text":"inert"},"commandId":"original","clientId":"fixture","origin":"ui"}
    async def callback(self, method, args):
        if KIND=="workspace":
            if method=="workspaceProjectionStatus":return {"revision":0}
            if method=="projectWorkspaces":return {"revision":args["throughRevision"]}
            raise AssertionError(method)
        if method=="readCoordinationSession":return {"status":"working","results":[],"latestSequence":0,"canFollowup":True}
        if method=="controlCoordinationSession":
            self.calls+=1
            return {"accepted":False,"disposition":"unknown"}
        raise AssertionError(method)
    def open(self):
        owner=Owner(self.config,self.callback) if KIND=="workspace" else Owner(self.config,self.callback,noop)
        if KIND=="workspace":
            def allocate(plan):
                self.calls+=1
                raise RuntimeError("inert unconfirmed allocation")
            owner.allocate=allocate
        return owner
    async def original(self,owner):
        try:await owner.request("action",self.args)
        except ValueError as e:
            assert KIND=="workspace" and getattr(e,"code",None)=="unknown_outcome"
        assert self.calls==1
    async def receipt(self,owner):
        value=await owner.request("action",{"operation":"workspace.receipt" if KIND=="workspace" else "coordination.command","args":{"commandId":"original"},"clientId":"fixture"})
        return value if KIND=="workspace" else value["receipt"]

async def noop(*args):pass

def readonly(path):
    db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
    try:
        return {"tables":[r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")],"version":db.execute("PRAGMA user_version").fetchone()[0]}
    finally:db.close()

def change(path,sql):
    readonly(path)
    with sqlite3.connect(path) as db:db.execute(sql)

def evidence(path):
    return {suffix:hashlib.sha256(Path(str(path)+suffix).read_bytes()).hexdigest() for suffix in ("","-wal","-journal") if Path(str(path)+suffix).exists()}

def refused(f):
    before=evidence(f.main)
    for _ in range(2):
        with pytest.raises(ValueError,match="authority schema"):f.open()
        after=evidence(f.main)
        assert all(after.get(suffix)==value for suffix,value in before.items())
    assert f.calls<=1

async def test_unknown_and_pending_original_receipt_never_repeats(tmp_path):
    f=Fixture(tmp_path);owner=f.open()
    await f.original(owner);receipt=await f.receipt(owner);assert receipt["status"]=="unknown"
    await owner.close()
    sql="UPDATE commands SET status='admitted' WHERE id='original'" if KIND=="workspace" else "UPDATE commands SET body=json_set(body,'$.status','dispatching') WHERE id='original'"
    change(f.main,sql)
    owner=f.open()
    try:
        assert (await f.receipt(owner))["status"]=="unknown"
        if KIND=="workspace":
            with pytest.raises(ValueError,match="unresolved"):await owner.request("action",f.args)
        else:
            assert (await owner.request("action",f.args))["receipt"]["status"]=="unknown"
        assert f.calls==1
    finally:await owner.close()

@pytest.mark.parametrize("table",TABLES)
async def test_missing_original_table_refuses_without_empty_repair(tmp_path,table):
    f=Fixture(tmp_path);owner=f.open();await f.original(owner);await owner.close()
    change(f.main,"DROP TABLE "+table);assert table not in readonly(f.main)["tables"]
    refused(f)

@pytest.mark.parametrize("suffix",["-wal","-shm","-journal"])
@pytest.mark.parametrize("payload",[b"",b"original evidence"])
async def test_absent_main_with_any_orphan_sidecar_refuses_before_writes(tmp_path,suffix,payload):
    f=Fixture(tmp_path);f.state.mkdir();sidecar=Path(str(f.main)+suffix);sidecar.write_bytes(payload)
    refused(f)
    assert not f.main.exists() and sidecar.read_bytes()==payload
    assert not (f.state/("intake.sqlite" if KIND=="workspace" else "intake.sqlite3")).exists()

async def test_dangling_main_refuses_and_does_not_create_symlink_target(tmp_path):
    f=Fixture(tmp_path);f.state.mkdir();target=f.state/"missing-target";f.main.symlink_to(target)
    for _ in range(2):
        with pytest.raises(ValueError,match="authority schema"):f.open()
    assert f.main.is_symlink() and not target.exists()

async def test_absent_original_main_with_empty_wal_does_not_redispatch(tmp_path):
    f=Fixture(tmp_path);owner=f.open();await f.original(owner);await owner.close();readonly(f.main)
    original=f.main.with_suffix(".retained");f.main.rename(original);before=original.read_bytes();Path(str(f.main)+"-wal").write_bytes(b"")
    refused(f);assert original.read_bytes()==before and not f.main.exists() and f.calls==1

@pytest.mark.parametrize("sql",["PRAGMA user_version=2","ALTER TABLE commands RENAME COLUMN id TO missing_id"])
async def test_unsupported_schema_refuses_before_writable_open(tmp_path,sql):
    f=Fixture(tmp_path);owner=f.open();await owner.close();change(f.main,sql);refused(f)

async def test_existing_empty_database_is_not_a_new_profile(tmp_path):
    f=Fixture(tmp_path);f.state.mkdir();sqlite3.connect(f.main).close();refused(f)

async def test_interrupted_committed_wal_is_preserved_on_refusal(tmp_path):
    f=Fixture(tmp_path);owner=f.open();await f.original(owner);await owner.close();readonly(f.main)
    script="import sqlite3,os,sys;db=sqlite3.connect(sys.argv[1]);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0');db.execute('DROP TABLE commands');db.commit();os._exit(0)"
    result=subprocess.run([sys.executable,"-I","-B","-c",script,str(f.main)],capture_output=True)
    assert result.returncode==0,result.stderr
    assert Path(str(f.main)+"-wal").stat().st_size>0
    assert "commands" not in readonly(f.main)["tables"]
    refused(f);assert f.calls==1

async def test_healthy_held_intake_and_receipt_continue_after_restart(tmp_path):
    f=Fixture(tmp_path);owner=f.open();await f.original(owner)
    binding={"fenceId":"original-fence","commandId":"update","purpose":"distribution-update","instanceId":"fixture","dataScope":"owned"}
    method="quiescence/acquire" if KIND=="workspace" else "quiescence.acquire"
    assert (await owner.request(method,binding))["acquired"]
    await owner.close();owner=f.open()
    try:
        method="quiescence/inspect" if KIND=="workspace" else "quiescence.inspect"
        assert (await owner.request(method,{}))["intakeClosed"]
        assert (await f.receipt(owner))["status"]=="unknown" and f.calls==1
    finally:await owner.close()

async def test_known_complete_unversioned_profile_upgrades(tmp_path):
    f=Fixture(tmp_path);f.state.mkdir()
    db=sqlite3.connect(f.main)
    if KIND=="workspace":
        db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);CREATE TABLE registrations(id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,name TEXT NOT NULL,hidden INTEGER NOT NULL,revision INTEGER NOT NULL);CREATE TABLE plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL,command_id TEXT);CREATE TABLE commands(id TEXT PRIMARY KEY,operation TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT,updated REAL NOT NULL);INSERT INTO meta VALUES('source','unified-workspaces:original-profile');INSERT INTO meta VALUES('revision','0');")
    else:db.execute("CREATE TABLE commands(id TEXT PRIMARY KEY,signature TEXT,body TEXT)")
    db.commit();db.close();assert readonly(f.main)["version"]==0
    owner=f.open()
    try:
        if KIND=="workspace":assert owner.source=="unified-workspaces:original-profile" and owner.config_sequence==0
        assert readonly(f.main)["version"]==1 and f.calls==0
    finally:await owner.close()

@pytest.mark.parametrize("sql",["DELETE FROM meta WHERE key='source'","DELETE FROM meta WHERE key='revision'","DELETE FROM meta WHERE key='configSequence'","UPDATE meta SET value='-1' WHERE key='revision'","UPDATE meta SET value='corrupt' WHERE key='configSequence'"])
async def test_required_metadata_cannot_be_silently_reinitialized(tmp_path,sql):
    f=Fixture(tmp_path);owner=f.open();await owner.close();change(f.main,sql);refused(f)

async def test_unversioned_later_profile_still_requires_config_sequence(tmp_path):
    f=Fixture(tmp_path);owner=f.open();await owner.close()
    change(f.main,"PRAGMA user_version=0");change(f.main,"DELETE FROM meta WHERE key='configSequence'")
    refused(f)
