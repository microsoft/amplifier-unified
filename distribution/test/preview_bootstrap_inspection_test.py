import importlib.util,pathlib,tempfile,sqlite3,shutil,hashlib,json,stat,os,unittest
spec=importlib.util.spec_from_file_location('probe',pathlib.Path(__file__).parents[1]/'src/preview_bootstrap_inspection.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=pathlib.Path(self.temp.name);self.data=self.root/'state';self.data.mkdir();self.before=self.root/'baseline.sqlite';self.db=self.data/'host.sqlite'
  c=sqlite3.connect(self.db);c.executescript('CREATE TABLE commands(id TEXT,status TEXT);CREATE TABLE receipts(command_id TEXT,payload TEXT);CREATE TABLE duplicates(value TEXT);INSERT INTO duplicates VALUES("a"),("a"),("b");')
  for n in range(61):c.execute('INSERT INTO receipts VALUES(?,?)',(str(n),json.dumps({'status':'unknown' if n<5 else 'completed'})))
  c.commit();c.close();shutil.copy2(self.db,self.before)
  self.policy={'databases':[{'path':str(self.db),'baseline':{'path':str(self.before),'sha256':self.sha(self.before)},'tables':{'commands':'empty-effect-journal','receipts':'preserved-history','duplicates':'preserved-history'},'hostReceipts':{'completed':56,'unknown':5}}],
   'roots':[{'id':'app','path':str(self.data)}],'entries':{}}
  for p in [self.data,self.db]:
   s=p.stat();d={'kind':'directory' if p.is_dir() else 'file','mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid}
   if p.is_file():d['database']=True
   self.policy['entries'][str(p)]=d
 def sha(self,p):return hashlib.sha256(p.read_bytes()).hexdigest()
 def mutate(self,sql):
  with sqlite3.connect(self.db) as c:c.executescript(sql)
 def test_preserves_five_unknown_receipts(self):
  before=self.db.read_bytes();v=probe.databases(self.policy)
  self.assertEqual(v['historicalUnknown']['count'],5);self.assertEqual(self.db.read_bytes(),before)
 def test_unknown_relabel_refuses(self):
  self.mutate("""UPDATE receipts SET payload='{"status":"completed"}' WHERE command_id='0'""")
  with self.assertRaisesRegex(RuntimeError,'state_changed'):probe.databases(self.policy)
 def test_duplicate_multiset_change_refuses(self):
  self.mutate('DELETE FROM duplicates;INSERT INTO duplicates VALUES("a"),("b"),("b");')
  with self.assertRaisesRegex(RuntimeError,'state_changed'):probe.databases(self.policy)
 def test_new_effect_refuses_even_with_matching_untrusted_baseline(self):
  self.mutate('INSERT INTO commands VALUES("x","unknown");');shutil.copy2(self.db,self.before)
  self.policy['databases'][0]['baseline']['sha256']=self.sha(self.before)
  with self.assertRaisesRegex(RuntimeError,'effect_journal_nonempty'):probe.databases(self.policy)
 def test_changed_schema_refuses(self):
  self.mutate('CREATE TABLE extra(command TEXT);')
  with self.assertRaisesRegex(RuntimeError,'state_changed'):probe.databases(self.policy)
 def test_missing_table_classification_refuses(self):
  del self.policy['databases'][0]['tables']['commands']
  with self.assertRaisesRegex(RuntimeError,'schema_uncovered'):probe.databases(self.policy)
 def test_baseline_tampering_refuses(self):
  with self.before.open('ab') as f:f.write(b'x')
  with self.assertRaisesRegex(RuntimeError,'artifact_changed'):probe.databases(self.policy)
 def test_nonempty_wal_refuses(self):
  pathlib.Path(str(self.db)+'-wal').write_bytes(b'pending writes')
  with self.assertRaisesRegex(RuntimeError,'nonempty_wal'):probe.databases(self.policy)
 def test_unreviewed_file_refuses(self):
  (self.data/'new.json').write_text('{}')
  with self.assertRaisesRegex(RuntimeError,'unreviewed_file'):probe.inventory(self.policy)
 def test_inventory_covers_every_database(self):
  self.assertEqual(probe.inventory(self.policy)['coverage'],'complete')
  self.policy['databases'].append({'path':str(self.data/'uncovered.sqlite')})
  with self.assertRaisesRegex(RuntimeError,'inventory_incomplete'):probe.inventory(self.policy)
 def test_absent_new_owner_state_is_not_created(self):
  p=self.data/'absent.sqlite';self.policy['databases'].append({'path':str(p),'absent':True})
  probe.databases(self.policy);self.assertFalse(p.exists())
  p.write_bytes(b'')
  with self.assertRaisesRegex(RuntimeError,'absent_owner_state_created'):probe.databases(self.policy)
if __name__=='__main__':unittest.main()
