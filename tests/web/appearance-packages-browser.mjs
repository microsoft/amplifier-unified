// Prepared-browser acceptance for Reference-based ZIP v2 packages only.
// No build, dependency install, provider call, or live-user data is used here.
import {spawn,spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {appendFileSync,existsSync,lstatSync,mkdirSync,mkdtempSync,readFileSync,readdirSync,realpathSync,writeFileSync} from 'node:fs';
import {isAbsolute,join,relative,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {createAppearanceRequestObserver} from './appearance-request-observer.mjs';

const root=realpathSync(fileURLToPath(new URL('../../',import.meta.url)));
const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
const within=(parent,path)=>{const rel=relative(parent,path);return rel===''||(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith('..'+sep))};
assert.ok(process.env.AMPLIFIER_TEST_CONTRACT,'An owned prepared browser-test execution contract is required.');
const contractPath=realpathSync(process.env.AMPLIFIER_TEST_CONTRACT);
const contractBytes=readFileSync(contractPath),executionContract=JSON.parse(contractBytes);
assert.equal(executionContract.profileId,'browser-test');
assert.equal(realpathSync(executionContract.workspace),root);
assert.ok(isAbsolute(executionContract.python));
const python=executionContract.python;
if(process.env.AMPLIFIER_TEST_PYTHON)assert.equal(realpathSync(process.env.AMPLIFIER_TEST_PYTHON),realpathSync(python));
const artifactRoot=realpathSync(executionContract.artifacts);
if(process.env.AMPLIFIER_TEST_ARTIFACTS)assert.equal(realpathSync(process.env.AMPLIFIER_TEST_ARTIFACTS),artifactRoot);
assert.ok(!within(root,artifactRoot),'Package fixtures and receipts stay outside the repository.');
const artifacts=mkdtempSync(join(artifactRoot,'appearance-packages-'));
const report={status:'running',executionContract,contractSha256:sha256(contractBytes),phases:[],downloads:[],
 limitations:['ZIP fixtures are synthetic PNG and text notices only; no external assets or fonts are used.']};
writeFileSync(join(artifacts,'execution-contract.json'),contractBytes);
function treeHashes(paths,{source=false}={}){
 const result={};
 function visit(path){
  assert.ok(within(root,realpathSync(path)));
  for(const entry of readdirSync(path,{withFileTypes:true})){
   const child=join(path,entry.name);
   if(source&&(entry.name==='__pycache__'||entry.name==='node_modules'||relative(root,child)==='amplifier_web'+sep+'static'))continue;
   assert.ok(!entry.isSymbolicLink(),'Identity inputs may not be symlinks: '+child);
   if(entry.isDirectory())visit(child);
   else if(entry.isFile())result[relative(root,child).split(sep).join('/')]=sha256(readFileSync(child));
  }
 }
 for(const name of paths)visit(join(root,name));
 return result;
}
const sourcePaths=['.build/web-client/src','.build/web-client/tests','tests/web','amplifier_web','tests/fixtures'];
const sourceHashes=()=>treeHashes(sourcePaths,{source:true});
const staticHashes=()=>treeHashes(['amplifier_web/static']);
report.sourceFiles=sourceHashes();report.buildFiles=staticHashes();

// The fixture writes and inspects actual ZIP32 bytes using only Python stdlib.
const zipProgram=String.raw`
import base64, binascii, hashlib, json, pathlib, struct, sys, zipfile, zlib
mode, directory = sys.argv[1], pathlib.Path(sys.argv[2])
colors = ((229, 37, 117), (18, 190, 213), (242, 173, 31), (94, 62, 205))
def png_chunk(kind, data):
 return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
rows = b"".join(b"\0" + b"".join(bytes(colors[(y // 16) * 2 + x // 16]) for x in range(32)) for y in range(32))
pixel = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
         + png_chunk(b"IDAT", zlib.compress(rows)) + png_chunk(b"IEND", b""))
notice = "Synthetic example asset license.\nPermission is granted to use this test pixel for qualification.\n"
manifest = {"type":"appearance","formatVersion":2,"base":"reference-v1","name":"Package studio",
 "description":"Synthetic Reference delta and embedded pixel.","author":"Synthetic author",
 "stylesheet":"theme.css","assets":[{"path":"assets/pixel.png","mediaType":"image/png","license":"licenses/EXAMPLE.txt"}],
 "licenses":["licenses/EXAMPLE.txt"]}
css = '''#amp-one {
 --a-bg:#f4f6f8; --a-surface:#ffffff; --a-ink:#203040;
 --a-muted:#536475; --a-line:#cbd5df; --a-accent:#365a83;
 background-color:var(--a-bg); color:var(--a-ink);
 background-image:url("assets/pixel.png");
 --a-conversation-art:url("assets/pixel.png");
}
'''
if mode == "create":
 files = {"appearance.json":json.dumps(manifest).encode(), "theme.css":css.encode(),
          "assets/pixel.png":pixel, "licenses/EXAMPLE.txt":notice.encode()}
 with zipfile.ZipFile(directory/"synthetic.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
  for name, body in files.items(): archive.writestr(name, body)
 print(json.dumps({"manifest":manifest,"pixelBase64":base64.b64encode(pixel).decode(),"license":notice}))
else:
 path, expected = pathlib.Path(sys.argv[3]), json.loads(sys.argv[4])
 raw = path.read_bytes()
 assert raw[:4] == b"PK\x03\x04", "Download is not a ZIP"
 assert len(raw) <= 4_000_000
 with zipfile.ZipFile(path) as archive:
  members = archive.infolist()
  assert len(members) <= 128 and len({m.filename for m in members}) == len(members)
  assert sum(m.file_size for m in members) <= 8_000_000
  assert archive.testzip() is None
  value = json.loads(archive.read("appearance.json"))
  assert value["type"] == "appearance" and value["formatVersion"] == 2
  assert value["base"] == "reference-v1", "Every portable package is a Reference delta"
  assert value["name"] == expected["name"]
  if "description" in expected: assert value.get("description") == expected["description"]
  if "author" in expected: assert value.get("author") == expected["author"]
  body = archive.read(value["stylesheet"]).decode("utf-8")
  declared = {"appearance.json",value["stylesheet"],*value.get("licenses",[]),
              *(asset["path"] for asset in value.get("assets",[]))}
  assert {m.filename for m in members if not m.is_dir()} == declared
  if expected.get("asset"):
   asset = next(row for row in value["assets"] if row["path"] == "assets/pixel.png")
   assert asset == manifest["assets"][0]
   assert archive.read(asset["path"]) == pixel, "Update dropped or changed the original asset"
  if expected.get("license"):
   assert "licenses/EXAMPLE.txt" in value["licenses"]
   assert archive.read("licenses/EXAMPLE.txt").decode() == notice, "Update dropped or changed the notice"
  if expected.get("original"):
   assert value == manifest, "Export changed the original manifest"
   assert body == css, "Export changed the original package stylesheet"
   assert 'url("assets/pixel.png")' in body, "Original package must retain its declared local asset URL"
  if expected.get("embedded"):
   assert "data:image/png;base64," + base64.b64encode(pixel).decode() in body
  if expected.get("marker"): assert expected["marker"] in body
  print(json.dumps({"manifest":value,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),
   "members":[{"path":m.filename,"bytes":m.file_size} for m in members]}))
`;
writeFileSync(join(artifacts,'zip-fixture.py'),zipProgram);
function zip(mode,...args){
 const result=spawnSync(python,['-c',zipProgram,mode,artifacts,...args],{encoding:'utf8',timeout:10000,killSignal:'SIGKILL',maxBuffer:2_000_000});
 appendFileSync(join(artifacts,'zip-helper.stderr.log'),result.stderr||'');
 if(result.error)throw result.error;
 assert.equal(result.status,0,'stdlib ZIP helper failed: '+result.stderr);
 return JSON.parse(result.stdout);
}

let browser,page,fixture,stop,failure;
const observer=createAppearanceRequestObserver(),pageErrors=[];
const state=()=>page.evaluate(()=>window.amplifier.getState());
const customPage=()=>page.locator('.a-settings-experience[data-settings-page="custom-appearance"] .a-settings-page-content:not([hidden])');
const action=async(name,args={})=>page.evaluate(async([name,args])=>{
 const receipt=await window.amplifier.dispatch(name,args);
 if(receipt.accepted!==true)throw Error(receipt.error||name+' rejected');
 return receipt;
},[name,args]);
const list=async()=>(await action('theme.list')).result.items;
const read=async id=>(await action('theme.read',{id})).result;
function libraryStorage(directory){
 assert.ok(within(artifacts,directory),'Read only the owned fixture library.');
 const entries={};
 function visit(path){
  if(!existsSync(path)){entries[relative(directory,path)]='absent';return}
  const info=lstatSync(path);assert.ok(!info.isSymbolicLink(),'Fixture library must not contain links.');
  assert.ok(within(artifacts,realpathSync(path)));
  if(info.isDirectory()){
   entries[relative(directory,path)]='directory';
   for(const name of readdirSync(path).sort())visit(join(path,name));
  }else{assert.ok(info.isFile());entries[relative(directory,path)]=sha256(readFileSync(path))}
 }
 visit(directory);return entries;
}
const preferenceIdentity=async()=>{
 const {activeScope,shared,local,effective,source,writeScope,theme,preview}=(await action('appearance.preferences.read')).result;
 return {activeScope,shared,local,effective,source,writeScope,theme,preview};
};
function actionResponse(name){
 return page.waitForResponse(response=>{
  try{return observer.matches(response.request(),name)}catch{return false}
 });
}
async function upload(file){
 const pending=actionResponse('theme.import');
 await customPage().locator('#theme-import').setInputFiles(file);
 const response=await pending;
 assert.ok(response.ok());
 const receipt=await response.json(),payload=observer.inspect(response.request());
 observer.assertHealthy();
 assert.deepEqual(payload.args,{packageBase64:file.buffer.toString('base64')},
  'ZIP v2 import carries only packageBase64; no CSS, filename fallback or format flag.');
 assert.equal(receipt.accepted,true);
 await expect(customPage().locator('.a-appearance-notice')).toContainText('added to your library.');
 return receipt;
}
async function exportZip(id,itemName,filename,expected){
 const pendingDownload=page.waitForEvent('download'),pendingResponse=actionResponse('theme.export');
 const item=customPage().locator('.a-appearance-saved > .a-appearance-saved-row').filter({hasText:itemName});
 await expect(item.getByRole('button',{name:'Export ZIP',exact:true}),
  'Saved package export must be reachable in Custom appearance, not hidden with ordinary preferences.').toBeVisible();
 await item.getByRole('button',{name:'Export ZIP',exact:true}).click();
 const [download,response]=await Promise.all([pendingDownload,pendingResponse]);
 assert.ok(response.ok());assert.equal((await response.json()).accepted,true);
 assert.deepEqual(observer.inspect(response.request()).args,{id},'ZIP export requires exactly one library ID.');
 assert.equal(await download.failure(),null);assert.match(download.suggestedFilename(),/\.zip$/i);
 const path=join(artifacts,filename);await download.saveAs(path);
 const bytes=readFileSync(path),verification=zip('verify',path,JSON.stringify(expected));
 assert.equal(verification.sha256,expected.fingerprint,'Export must preserve the exact saved ZIP bytes, not recompile its stylesheet.');
 report.downloads.push({name:itemName,path,bytes:bytes.length,sha256:sha256(bytes),verification});
 return verification;
}

try{
 const synthetic=zip('create');
 const packageFile={name:'unrelated-name.zip',mimeType:'application/zip',buffer:readFileSync(join(artifacts,'synthetic.zip'))};
 const fixtureTemp=join(artifacts,'tmp'),fixtureHome=join(artifacts,'home');
 mkdirSync(fixtureTemp);mkdirSync(fixtureHome);
 const env={...process.env,TMPDIR:fixtureTemp,HOME:fixtureHome,PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_TEST_INSTALLED:'0'};
 for(const name of ['AMPLIFIER_TEST_SHARED_WORK_HOME','AMPLIFIER_TEST_PACKAGE_ROOT','AMPLIFIER_TEST_INSTALLED_IDENTITY'])delete env[name];
 fixture=spawn(python,['-u',join(root,'tests/fixtures/empty_host_ui_server.py')],{cwd:root,env,stdio:['ignore','pipe','pipe']});
 stop=ownFixture(fixture,{label:'Appearance packages fixture',timeoutMs:10000});
 fixture.stderr.on('data',bytes=>appendFileSync(join(artifacts,'fixture.stderr.log'),bytes));
 const url=await new Promise((resolve,reject)=>{
  let output='',settled=false;const timer=setTimeout(()=>finish(Error('Appearance fixture startup timed out')),20000);
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value)};
  fixture.once('error',error=>finish(error));fixture.once('exit',(code,signal)=>finish(Error('Fixture exited '+(code??signal))));
  fixture.stdout.on('data',bytes=>{appendFileSync(join(artifacts,'fixture.stdout.log'),bytes);output+=bytes;
   let newline;while((newline=output.indexOf('\n'))>=0){const line=output.slice(0,newline);output=output.slice(newline+1);
    try{const value=JSON.parse(line);if(value.url)finish(null,value.url)}catch{}
   }
  });
 });
 report.fixtureUrl=url;
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1360,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('request',request=>observer.observe(request));
 await page.goto(url);await showFixtureConversation(page);await action('session.create');
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.fill('Keep this synthetic conversation draft.');
 const selected=(await state()).selectedSessionId,before=(await state()).appearance.theme;
 await openSettingsPage(page,'custom-appearance');
 await expect(customPage().locator('#theme-import')).toHaveAttribute('accept','.zip,application/zip');
 const beforeInvalidLibrary=(await action('theme.list')).result;
 const beforeInvalidStorage=libraryStorage(beforeInvalidLibrary.directory),beforeInvalidPreferences=await preferenceIdentity();
 const cssCount=observer.actions.filter(row=>row.action==='theme.import').length;
 const invalidResponse=actionResponse('theme.import');
 const rawCssError=await page.evaluate(async()=>{
  try{await window.amplifier.dispatch('theme.import',{css:'#amp-one{--a-accent:#123456}'})}
  catch(error){return error.message}
  return null;
 });
 assert.equal(rawCssError,"'packageBase64' is a required property",'Standalone CSS import must throw the exact schema rejection.');
 const rejected=await invalidResponse,rejection=await rejected.json();
 assert.equal(rejected.status(),400);assert.equal(rejection.accepted,false);
 assert.equal(rejection.error,"'packageBase64' is a required property");
 assert.deepEqual(observer.inspect(rejected.request()).args,{css:'#amp-one{--a-accent:#123456}'},
  'The public bridge submits the malformed intent unchanged; there is no legacy import conversion.');
 observer.assertHealthy();
 assert.equal(observer.actions.filter(row=>row.action==='theme.import').length,cssCount+1,
  'Exactly one public-bridge import reaches server validation; rejection must not retry or use a compatibility path.');
 assert.deepEqual((await action('theme.list')).result,beforeInvalidLibrary,'Rejected CSS cannot add or alter any library entry.');
 assert.deepEqual(libraryStorage(beforeInvalidLibrary.directory),beforeInvalidStorage,'Rejected CSS cannot create or alter library storage.');
 assert.deepEqual(await preferenceIdentity(),beforeInvalidPreferences,'Rejected CSS cannot change shared/local preferences, selection or preview.');
 report.phases.push('server-rejects-standalone-css-without-library-storage-or-preference-mutation');

 const imported=await upload(packageFile);
 const entry=(await list()).find(row=>row.source==='saved'&&row.name==='Package studio');
 assert.ok(entry);assert.notEqual(entry.name,packageFile.name);
 assert.equal(imported.result.id,entry.id);
 const original=await read(entry.id);
 assert.equal(original.base,'reference-v1');assert.equal(original.format,'zip');
 assert.ok(original.css.includes('data:image/png;base64,'+synthetic.pixelBase64));
 assert.deepEqual((await state()).appearance.theme,before,'Import adds a library ID without selecting it.');
 const edit=customPage().getByRole('button',{name:'Edit Package studio',exact:true});
 await expect(edit,'Imported saved packages must expose an Edit control in Custom appearance.').toBeVisible();
 await edit.click();
 await exportZip(entry.id,'Package studio','package-original.zip',{
  name:'Package studio',description:'Synthetic Reference delta and embedded pixel.',
  author:'Synthetic author',asset:true,license:true,original:true,fingerprint:original.fingerprint,
 });
 report.phases.push('zip-v2-import-and-exact-id-export');

 const revised={name:'Package studio revised',description:'Updated delta; original asset and notice retained.'};
 await customPage().locator('#theme-name').fill(revised.name);
 await customPage().locator('#theme-description').fill(revised.description);
 const editedCss=original.css+'\n#amp-one { --a-accent: #587a9b; }\n/* package-update-marker */\n';
 await customPage().locator('#theme-css').fill(editedCss);
 const updateResponse=actionResponse('theme.update');
 await customPage().getByRole('button',{name:'Update saved appearance',exact:true}).click();
 const response=await updateResponse;
 assert.ok(response.ok());
 const updateReceipt=await response.json(),update=observer.inspect(response.request());
 observer.assertHealthy();
 assert.equal(updateReceipt.accepted,true);
 assert.deepEqual(update.args,{
  id:entry.id,expectedFingerprint:original.fingerprint,name:revised.name,
  description:revised.description,css:editedCss,
 },'In-place package update is bound to the exact read fingerprint and edited delta.');
 await expect(customPage().locator('.a-appearance-notice')).toContainText('Library entry updated.');
 const updated=await read(entry.id);
 assert.equal(updated.id,entry.id);assert.equal(updated.name,revised.name);
 assert.equal(updated.description,revised.description);
 assert.notEqual(updated.fingerprint,original.fingerprint);
 assert.deepEqual((await state()).appearance.theme,before,'Updating a library package cannot select it.');
 await exportZip(entry.id,revised.name,'package-updated.zip',{
  name:revised.name,description:revised.description,author:'Synthetic author',
  asset:true,license:true,embedded:true,marker:'package-update-marker',fingerprint:updated.fingerprint,
 });
 report.phases.push('exact-fingerprint-update-preserves-assets-and-license-notice');

 const use=customPage().getByRole('button',{name:'Use '+revised.name,exact:true});
 await use.click();
 await expect.poll(async()=>(await state()).appearance.theme.name).toBe(revised.name);
 assert.equal((await state()).selectedSessionId,selected);
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).backgroundImage))
  .toContain('data:image/png;base64,'+synthetic.pixelBase64);
 await expect(composer).toHaveDraft('Keep this synthetic conversation draft.');
 assert.deepEqual(pageErrors,[]);
 assert.deepEqual((await page.request.get(url+'/fixture').then(result=>result.json())).sent,[]);
 report.status='passed';
 console.log(JSON.stringify({status:report.status,artifacts,phases:report.phases}));
}catch(error){
 failure=error;report.status='failed';report.error=error.stack||String(error);
 if(page&&!page.isClosed())try{await page.screenshot({path:join(artifacts,'failure.png'),timeout:2000})}catch{}
}finally{
 try{
  await finishFixture({label:'Appearance packages fixture',originalError:failure,
   close:[['Appearance packages browser',async()=>{await browser?.close()}]],
   stop:async()=>{if(stop)await stop()},
  });
 }catch(error){failure??=error;report.cleanupError=error.stack||String(error)}
 report.pageErrors=pageErrors;report.requestObserver=observer.evidence();
 try{observer.assertHealthy()}catch(error){failure??=error;report.observerError=error.message}
 assert.deepEqual(sourceHashes(),report.sourceFiles,'Authored source changed during package qualification.');
 assert.deepEqual(staticHashes(),report.buildFiles,'Built assets changed during package qualification.');
 report.status=failure?'failed':'passed';
 writeFileSync(join(artifacts,'report.json'),JSON.stringify(report,null,2)+'\n');
}
if(failure)throw failure;