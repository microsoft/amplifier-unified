// One Chromium owner per guest, reaching only that guest's real loopback app.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {constants} from 'node:fs';
import {access,readFile,rename,stat,unlink,writeFile} from 'node:fs/promises';
import {request as httpsRequest} from 'node:https';
import {createConnection} from 'node:net';
import {setTimeout as delay} from 'node:timers/promises';
import {promisify} from 'node:util';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';

const role = process.argv[2];
const run = process.argv[3];
assert.ok(['source','destination'].includes(role));
assert.match(run,/^[a-f0-9]{32}$/);
const own = `/run/link-${role}`;
const other = `/run/link-${role === 'source' ? 'destination' : 'source'}`;
const invitationFile = '/run/link-control/fresh-invitation.json';
const prefix = '/api/linked-instances/v1/';
const checkpoints = [];
let stage = 'authenticated local browser', browser;
const result = {role,run,checkPassed:false,checkpoints};
const exec = promisify(execFile);
const button = (page,name) => page.getByRole('button',{name,exact:true});
const wait = async (path, predicate = () => true) => {
  for (let n=0;n<240;n++) {
    try {
      const item = JSON.parse(await readFile(path,'utf8'));
      if (predicate(item)) return item;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    await delay(125);
  }
  throw new Error('Timed out waiting for owned guest witness');
};
const waitSignal = async path => {
  const witness = await wait(path);
  assert.equal(witness.run,run,'Guest witness belongs to a different run');
  return witness;
};
async function publish(path, value) {
  await writeFile(`${path}.next`,JSON.stringify({...value,run})+'\n',{flag:'wx',mode:0o644});
  await rename(`${path}.next`,path);
}
async function localApi(page, path, body) {
  return page.evaluate(async ({path,body}) => {
    const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    return {http:response.status, ...await response.json()};
  },{path:prefix+path,body});
}
async function clickReceipt(page, control, operation) {
  const [reply] = await Promise.all([
    page.waitForResponse(r => r.request().method()==='POST' && new URL(r.url()).pathname===prefix+operation),
    control.click(),
  ]);
  return {http:reply.status(),request:reply.request().postDataJSON(),...await reply.json()};
}
async function mount(page) {
  await openSettingsPage(page,'linked-instances',{activate:(_label,control,options) => control.click(options)});
  await expect(page.getByRole('region',{name:'Linking setup',exact:true})).toBeVisible();
}
async function signedInTab(context, host, login = true) {
  const page = await context.newPage();
  const base = host.base;
  page.setDefaultTimeout(8000);
  await page.goto(base);
  if (login) {
    await page.waitForURL(base+'/login**');
    assert.equal(await page.evaluate(() => fetch('/api/state').then(r=>r.status)),401);
    assert.equal((await localApi(page,'setup',{})).http,401);
    await page.getByLabel('Username').fill(`fixture-${role}`);
    await page.getByLabel('Password').fill(`synthetic-only-${role}`);
    await button(page,'Sign in').click();
  }
  await page.waitForURL(base+'/');
  assert.equal(await page.evaluate(() => fetch('/api/state').then(r=>r.status)),200);
  await mount(page);
  return page;
}
async function setup(page) {
  const readback = await localApi(page,'setup',{});
  assert.equal(readback.http,200);
  assert.match(readback.result.revision,/^[a-f0-9]{64}$/);
  return readback.result;
}
async function links(page) {
  const readback = await localApi(page,'list',{});
  assert.equal(readback.http,200);
  return readback.result;
}
async function retryWitness(page) {
  const counters = {streamRequests:0,streamResponses:0,streamEnds:0,streamFailures:0,mainNavigations:0};
  const stream = request => new URL(request.url()).pathname === '/api/events';
  page.on('request', request => {if (stream(request)) counters.streamRequests++;});
  page.on('response', response => {if (stream(response.request())) counters.streamResponses++;});
  page.on('requestfinished', request => {if (stream(request)) counters.streamEnds++;});
  page.on('requestfailed', request => {if (stream(request)) counters.streamFailures++;});
  page.on('framenavigated', frame => {if (frame===page.mainFrame()) counters.mainNavigations++;});
  await page.evaluate(() => {
    const key = Symbol.for('fresh-pair-fixture-mount-witness');
    const region = () => document.querySelector('.a-settings-experience [aria-label="Linking setup"]');
    let previous = region(), transitions = 0, reconnects = 0;
    window.addEventListener('amplifier-reconnected',() => reconnects++);
    const observer = new MutationObserver(() => {
      const now = region();
      if (now!==previous) { previous=now; transitions++; }
    });
    observer.observe(document.documentElement,{childList:true,subtree:true});
    window[key] = () => {
      const settings = document.querySelector('.a-settings-experience');
      return {transitions,reconnects,setupMounted:!!region(),
        route:!settings?'closed':settings.dataset.settingsPage==='linked-instances'?'linked-instances':'other-settings'};
    };
  });
  const state = () => page.evaluate(async () => {
    const view = window[Symbol.for('fresh-pair-fixture-mount-witness')]?.() ?? null;
    try {
      const response = await fetch('/api/state',{signal:AbortSignal.timeout(4000)});
      const owner = response.headers.get('X-Amplifier-History-Scope');
      await response.body?.cancel();
      return {view,document:performance.timeOrigin,status:response.status,owner};
    } catch {return {view,document:performance.timeOrigin,status:null,owner:null};}
  });
  const initial = await state();
  return async () => {
    const observed = await state();
    return {sameDocument:observed.document===initial.document,settings:observed.view,
      ownerStatus:observed.status,ownerScopePresent:!!observed.owner,
      sameOwnerScope:!!initial.owner&&observed.owner===initial.owner,...counters};
  };
}
function assertPeerOrigin(origin, host) {
  const address = new URL(origin);
  assert.equal(address.protocol,'https:');
  assert.equal(address.hostname,host.address);
  assert.equal(address.pathname,'/');
  assert.ok(Number(address.port)>0);
  assert.notEqual(Number(address.port),host.mainPort);
  return address;
}
async function enable(page, host) {
  const initial = await setup(page);
  assert.equal(initial.status,'off');
  assert.equal(initial.enabled,false);
  assert.equal(initial.peerListener,false);
  assert.equal(initial.listener,'none');
  assert.deepEqual(initial.origins,[]);
  assert.deepEqual(initial.discovery,{lan:false,tailscale:false});
  const hidden = await links(page);
  assert.deepEqual(hidden.origins,[]);
  assert.deepEqual(hidden.links,[]);
  await expect(button(page,'Create invitation')).toHaveCount(0);
  await page.getByText('Discovery options',{exact:true}).click();
  await page.getByRole('checkbox',{name:'Allow nearby discovery',exact:true}).check();
  const enabled = await clickReceipt(page,button(page,'Allow linking from other devices'),'setup-save');
  assert.equal(enabled.http,200);
  assert.deepEqual(enabled.request,{enabled:true,lan:true,tailscale:false,peerListener:true,expectedRevision:initial.revision});
  await expect(page.getByText('Linking is ready on this Amplifier.',{exact:true})).toBeVisible();
  const live = await setup(page);
  assert.equal(live.status,'active');
  assert.equal(live.enabled,true);
  assert.equal(live.listener,'peer-only');
  assert.equal(live.peerListener,true);
  assert.notEqual(live.revision,initial.revision);
  assert.deepEqual(live.blockers,[]);
  assert.deepEqual(live.discovery,{lan:true,tailscale:false});
  assert.equal(live.lanExpiresAt,null);
  assert.equal(live.origins.length,1);
  assertPeerOrigin(live.origins[0],host);
  checkpoints.push('fresh-loopback-owner-opt-in-opens-one-distinct-peer-origin-without-advertising');
  return live;
}

// Read only addresses from this immutable private guest topology. TLS
// fingerprint is compared before transmitting even the fixture's probe body.
function peerProbe(origin, fingerprint, path, {method='POST',body={},headers={}}={}) {
  assert.match(fingerprint,/^[a-f0-9]{64}$/);
  return new Promise((resolve,reject) => {
    const payload = JSON.stringify(body);
    const request = httpsRequest(origin+path,{
      agent:false,method,rejectUnauthorized:false,
      headers:{...(method==='POST' ? {'Content-Type':'application/json','Content-Length':Buffer.byteLength(payload)} : {}),...headers},
    }, response => {
      const chunks = [];
      let bytes = 0;
      response.on('error',reject);
      response.on('data',buffer => {
        bytes += buffer.length;
        if (bytes > 8192) request.destroy(new Error('Unexpected private probe response size'));
        else chunks.push(buffer);
      });
      response.on('end',() => resolve({http:response.statusCode,headers:response.headers,text:Buffer.concat(chunks).toString('utf8')}));
    });
    request.setTimeout(3000,() => request.destroy(new Error('Peer probe timed out')));
    request.on('error',reject);
    request.on('socket',socket => socket.once('secureConnect',() => {
      const raw = socket.getPeerCertificate()?.raw;
      if (!raw || createHash('sha256').update(raw).digest('hex')!==fingerprint) {
        request.destroy(new Error('Wrong private peer fingerprint'));
        return;
      }
      request.end(method==='POST' ? payload : undefined);
    }));
  });
}
const refuseTcp = (address,port) => assert.rejects(new Promise((resolve,reject) => {
  const socket = createConnection({host:address,port});
  socket.setTimeout(3000,() => socket.destroy(new Error('Owned TCP refusal timed out')));
  socket.on('error',reject);
  socket.on('connect',() => {socket.destroy();resolve();});
}),{code:'ECONNREFUSED'});

async function source(page, context, host, active) {
  stage = 'confirm native browse empty before owner advertises';
  assert.equal((await waitSignal(`${other}/fresh-mdns-baseline.json`)).empty,true);
  stage = 'source invitation and QR';
  const advertised = await clickReceipt(page,button(page,'Make this Amplifier discoverable'),'lan-discoverable');
  assert.equal(advertised.http,200);
  assert.deepEqual(advertised.request,{enabled:true});
  assert.equal(advertised.result.status,'ok');
  assert.ok(advertised.result.expiresAt > Date.now()/1000);
  await expect(button(page,'Stop being discoverable')).toBeVisible();
  await button(page,'Create invitation').click();
  const offer = await clickReceipt(page,button(page,'Create'),'offer');
  assert.equal(offer.http,200);
  assert.equal(offer.request.origin,active.origins[0]);
  const field = page.getByRole('textbox',{name:'Created invitation',exact:true});
  await expect(field).toBeVisible();
  const code = await field.inputValue(); // only in the guest-only transient rendezvous
  assert.ok(code.startsWith('amp-link-v1.'));
  await button(page,'Show QR code').click();
  const qr = page.getByRole('img',{name:'Amplifier pairing QR code',exact:true});
  await expect(qr).toBeVisible();
  assert.ok(await qr.evaluate(img=>img.decode().then(()=>img.naturalWidth))>0);
  checkpoints.push('issuer-qr-rendered-and-invitation-bound-to-private-origin');
  await publish(invitationFile,{code,origin:active.origins[0]});
  stage = 'issuer ledger contains the persisted grant before retry';
  const marker = await wait(`${own}/fresh-post-persist.json`);
  assert.deepEqual(Object.keys(marker),['grantId']);
  const half = (await links(page)).links;
  assert.deepEqual(half.map(row=>[row.grantId,row.direction,row.scope,row.revoked]),
    [[marker.grantId,'incoming','status',false]]);
  await publish(`${own}/fresh-issuer-half.json`,{grantId:marker.grantId});
  const granted = await waitSignal(`${other}/fresh-joined.json`);
  assert.equal(granted.grantId,marker.grantId);
  await unlink(invitationFile);
  const issuer = await links(page);
  const before = issuer.links;
  assert.equal(before.length,1);
  assert.deepEqual(before.map(row=>[row.grantId,row.direction,row.scope,row.revoked]),
    [[granted.grantId,'incoming','status',false]]);

  stage = 'explicit owner stops advertising before lease expires';
  assert.ok(Date.now()/1000 < advertised.result.expiresAt);
  const stopped = await clickReceipt(page,button(page,'Stop being discoverable'),'lan-discoverable');
  assert.equal(stopped.http,200);
  assert.deepEqual(stopped.request,{enabled:false});
  assert.equal((await setup(page)).lanExpiresAt,null);
  await publish(`${own}/fresh-mdns-stopped.json`,{expiresAt:advertised.result.expiresAt});
  assert.equal((await waitSignal(`${other}/fresh-mdns-withdrawn.json`)).empty,true);
  checkpoints.push('explicit-lan-lease-stopped-and-withdrawn-before-expiry');

  stage = 'two owner tabs retain exact setup revision';
  const stale = await signedInTab(context,host,false);
  const snapshot = await setup(stale);
  await stale.getByText('Discovery options',{exact:true}).click();
  await stale.getByRole('checkbox',{name:'Allow Tailscale discovery',exact:true}).check();
  const disabled = await clickReceipt(page,button(page,'Turn off linking'),'setup-save');
  assert.equal(disabled.http,200);
  assert.deepEqual(disabled.request,{enabled:false,lan:true,tailscale:false,expectedRevision:snapshot.revision});
  const off = await setup(page);
  assert.equal(off.enabled,false);
  assert.equal(off.listener,'none');
  assert.equal(off.peerListener,true);
  assert.deepEqual(off.origins,[]);
  assert.notEqual(off.revision,snapshot.revision);
  const conflict = await clickReceipt(stale,button(stale,'Save discovery options'),'setup-save');
  assert.deepEqual(conflict.request,{enabled:true,lan:true,tailscale:true,expectedRevision:snapshot.revision});
  assert.equal(conflict.http,409);
  assert.equal(conflict.code,'link_setup_conflict');
  assert.equal(conflict.accepted,false);
  assert.equal(conflict.result,undefined);
  await expect(stale.getByText('Linking settings changed. Refresh setup to review the current settings before saving again.',{exact:true})).toBeVisible();
  await expect(button(stale,'Save discovery options')).toHaveCount(0);
  assert.deepEqual(await setup(page),off);
  const reread = await clickReceipt(stale,button(stale,'Refresh setup'),'setup');
  assert.equal(reread.result.revision,off.revision);
  assert.deepEqual(await setup(page),off);
  await expect(stale.getByText('Linking is off on this Amplifier.',{exact:true})).toBeVisible();
  await publish(`${own}/fresh-disabled.json`,{revision:off.revision,listener:off.listener});
  assert.equal((await waitSignal(`${other}/fresh-closed.json`)).grantId,granted.grantId);
  checkpoints.push('disable-closes-peer-socket-and-stale-tab-cannot-reopen-or-change-options');

  stage = 'explicit owner re-enable and controlled issuer restart';
  const enabled = await clickReceipt(page,button(page,'Enable linking'),'setup-save');
  assert.equal(enabled.http,200);
  assert.equal(enabled.result.listener,'peer-only');
  assert.deepEqual(enabled.result.origins,active.origins);
  assert.deepEqual((await links(page)).links,before);
  await publish(`${own}/fresh-restart.json`,{afterExplicitOwnerEnable:true});
  const now = await wait(`${own}/fresh-ready.json`,item=>item.restarted);
  assert.notEqual(now.runningInstance,host.runningInstance);
  for (const key of ['base','address','mainPort','dataIdentity','clientRevision'])
    assert.equal(now[key],host[key]);
  const refreshed = await clickReceipt(page,button(page,'Refresh setup'),'setup');
  assert.equal(refreshed.http,200);
  await expect(page.getByText('Linking is ready on this Amplifier.',{exact:true})).toBeVisible();
  const restarted = await setup(page);
  assert.deepEqual(restarted.origins,active.origins);
  assert.equal(restarted.listener,'peer-only');
  assert.equal(restarted.lanExpiresAt,null);
  assert.equal((await links(page)).instanceId,issuer.instanceId);
  assert.deepEqual((await links(page)).links,before);
  await publish(`${own}/fresh-restarted.json`,{grantId:granted.grantId});
  assert.equal((await waitSignal(`${other}/fresh-checked.json`)).grantId,granted.grantId);
  checkpoints.push('new-issuer-process-preserves-main-loopback-peer-origin-instance-and-grant');
  stage = 'issuer revokes the same grant locally while peer listener is off';
  const turnedOff = await clickReceipt(page,button(page,'Turn off linking'),'setup-save');
  assert.equal(turnedOff.http,200);
  assert.equal(turnedOff.result.listener,'none');
  await expect(page.getByText('Linking is off on this Amplifier.',{exact:true})).toBeVisible();
  assert.deepEqual((await links(page)).links,before);
  await publish(`${own}/fresh-off-for-revoke.json`,{grantId:granted.grantId});
  assert.equal((await waitSignal(`${other}/fresh-off-confirmed.json`)).grantId,granted.grantId);
  await button(page,'Revoke link').click();
  const revoke = await clickReceipt(page,button(page,'Confirm revoke'),'revoke');
  assert.equal(revoke.http,200);
  assert.equal(revoke.request.grantId,granted.grantId);
  assert.equal(revoke.result.grantId,granted.grantId);
  assert.equal(revoke.result.revoked,true);
  assert.deepEqual((await links(page)).links,before.map(row=>({...row,revoked:true})));
  assert.equal((await setup(page)).listener,'none');
  await publish(`${own}/fresh-revoked-off.json`,{grantId:granted.grantId});
  assert.equal((await waitSignal(`${other}/fresh-revoked-off-confirmed.json`)).grantId,granted.grantId);
  checkpoints.push('owner-revokes-exact-saved-grant-locally-while-peer-socket-stays-closed');
  stage = 'issuer explicitly re-enables to verify remote scope stays revoked';
  const enabledAfterRevoke = await clickReceipt(page,button(page,'Enable linking'),'setup-save');
  assert.equal(enabledAfterRevoke.http,200);
  assert.equal(enabledAfterRevoke.result.listener,'peer-only');
  assert.deepEqual(enabledAfterRevoke.result.origins,active.origins);
  await publish(`${own}/fresh-revoked.json`,{grantId:granted.grantId});
  assert.equal((await waitSignal(`${other}/fresh-denied.json`)).grantId,granted.grantId);
  checkpoints.push('issuer-ui-revokes-exact-grant-and-joiner-confirms-denial');
}

async function nativeRecords() {
  const {stdout} = await exec('avahi-browse',['--terminate','--resolve','--parsable',
    '--no-db-lookup','--domain=local','_amplifier-link._tcp'],{timeout:5000,maxBuffer:16384});
  return stdout.split('\n').filter(line=>line.startsWith('=;')).map(line=>line.split(';').slice(1,9));
}

async function destination(page, host) {
  stage = 'native Avahi and GUI show no peer before explicit advertising';
  assert.deepEqual(await nativeRecords(),[]);
  const empty = await clickReceipt(page,button(page,'Find nearby Amplifiers'),'lan-browse');
  assert.equal(empty.http,200);
  assert.deepEqual(empty.result,{status:'empty',instances:[]});
  await publish(`${own}/fresh-mdns-baseline.json`,{empty:true});
  stage = 'local discovery and independent TCP/TLS boundary';
  const src = await wait(`${other}/fresh-ready.json`);
  const invitation = await waitSignal(invitationFile);
  const payload = JSON.parse(Buffer.from(invitation.code.split('.')[1],'base64url'));
  const addr = assertPeerOrigin(invitation.origin,src);
  assert.equal(payload.origin,addr.origin);
  assert.match(payload.instanceId,/^[a-f0-9]{32}$/);
  await expect.poll(async () => (await nativeRecords()).map(
    ([iface,family,name,type,domain,_dns,address,port])=>[iface,family,name,type,domain,address,port]),
    {timeout:15000}).toEqual([['eth0','IPv4',`Amplifier-${payload.instanceId}`,'_amplifier-link._tcp','local',src.address,addr.port]]);
  checkpoints.push('native-avahi-resolved-only-expected-peer-address-and-tls-port');
  await refuseTcp(src.address,src.mainPort);
  const mainPeerStatus = await page.evaluate(async path => (await fetch(path,{
    method:'POST',headers:{'Content-Type':'application/json'},body:'{}',
  })).status,prefix+'peer/identity');
  assert.ok([404,405].includes(mainPeerStatus));
  for (const path of ['/','/login','/api/state','/api/action','/api/health',prefix+'setup',prefix+'setup-save',prefix+'list']) {
    const probe = await peerProbe(addr.origin,payload.fingerprint,path);
    assert.equal(probe.http,404);
    assert.equal(probe.headers['set-cookie'],undefined);
  }
  const args = {instanceId:payload.instanceId};
  const identity = await peerProbe(addr.origin,payload.fingerprint,prefix+'peer/identity',{body:args});
  assert.equal(identity.http,200);
  assert.deepEqual(JSON.parse(identity.text),{result:args});
  assert.equal(identity.headers['set-cookie'],undefined);
  for (const headers of [{Cookie:'fixture=not-an-owner-cookie'},{Origin:host.base},{Authorization:'Bearer synthetic-not-a-link'}]) {
    const denied = await peerProbe(addr.origin,payload.fingerprint,prefix+'peer/identity',{body:args,headers});
    assert.equal(denied.http,403);
    assert.equal(denied.headers['set-cookie'],undefined);
  }
  checkpoints.push('cross-guest-main-tcp-refused-private-tls-pin-and-exact-peer-path-boundary-proven');
  const browsed = await clickReceipt(page,button(page,'Find again'),'lan-browse');
  assert.equal(browsed.http,200);
  assert.equal(browsed.result.status,'ok');
  const [candidate] = browsed.result.instances;
  assert.equal(browsed.result.instances.length,1);
  assert.equal(candidate.instanceId,payload.instanceId);
  assert.equal(candidate.route,'lan');
  assert.equal((await links(page)).links.length,0);
  await page.getByRole('list',{name:'Discovered Amplifiers',exact:true}).getByRole('button',{name:candidate.label,exact:true}).click();
  await button(page,'Join this Amplifier').click();
  const field = page.getByRole('textbox',{name:'Amplifier invitation',exact:true});
  await expect(field).toBeFocused();
  await field.fill(invitation.code);
  stage = 'issuer save survives an unconfirmed enrollment';
  const snapshot = await retryWitness(page);
  const joinPosts = [];
  const countJoin = request => {
    if (request.method()==='POST' && new URL(request.url()).pathname===prefix+'join')
      joinPosts.push(request.postDataBuffer());
  };
  page.on('request',countJoin);
  const retry = button(page,'Retry same request');
  const notice = page.getByRole('alert').filter({hasText:/^Amplifier couldn’t confirm this link\. The link may already have been created\./});
  let joined;
  try {
    await assert.rejects(stat(`${other}/fresh-post-persist.json`),{code:'ENOENT'});
    assert.equal(joinPosts.length,0);
    const original = await clickReceipt(page,button(page,'Join'),'join');
    assert.equal(original.http,503);
    assert.equal(original.code,'link_join_unconfirmed');
    assert.equal(original.result,undefined);
    const marker = await wait(`${other}/fresh-post-persist.json`);
    assert.match(marker.grantId,/^[a-f0-9]{32}$/);
    assert.deepEqual((await links(page)).links,[]);
    await expect(retry).toBeEnabled();
    await expect(notice).toBeVisible();
    const sample = async () => ({retryCount:await retry.count(),noticeCount:await notice.count(),...await snapshot()});
    result.recovery = {initialStatus:503,issuerPersisted:true,initial:await sample(),observationMs:2000};
    stage = 'unconfirmed retry remains available on the same owner document';
    await delay(result.recovery.observationMs);
    result.recovery.afterWait = await sample();
    result.recovery.beforeRetryPostCount = joinPosts.length;
    assert.equal(joinPosts.length,1);
    assert.deepEqual((await links(page)).links,[]);
    assert.equal((await waitSignal(`${other}/fresh-issuer-half.json`)).grantId,marker.grantId);
    assert.equal(result.recovery.afterWait.sameDocument,true);
    assert.equal(result.recovery.afterWait.sameOwnerScope,true);
    await expect(notice).toBeVisible();
    await expect(retry).toBeEnabled();
    stage = 'one explicit byte-identical retry confirms the persisted grant';
    joined = await clickReceipt(page,retry,'join');
    assert.equal(joined.result?.grantId,marker.grantId);
    assert.equal(joinPosts.length,2);
    assert.ok(joinPosts[0] && joinPosts[1] && joinPosts[0].equals(joinPosts[1]),'Exact Join request bytes changed');
    await expect(retry).toHaveCount(0);
    await expect(notice).toHaveCount(0);
    result.recovery.exactRetry = true;
    checkpoints.push('real-post-persistence-503-preserved-and-one-explicit-identical-retry-confirmed');
  } finally {
    page.off('request',countJoin);
    joinPosts.length = 0;
  }
  assert.equal(joined.http,200);
  assert.equal(joined.request.expectedInstanceId,payload.instanceId);
  assert.equal(joined.result.instanceId,payload.instanceId);
  assert.equal(joined.result.direction,'outgoing');
  assert.equal(joined.result.scope,'status');
  const grantId = joined.result.grantId;
  const check = () => clickReceipt(page,button(page,'Check availability'),'status');
  const online = await check();
  assert.equal(online.http,200);
  assert.equal(online.result.instanceId,payload.instanceId);
  assert.equal(online.result.availability,'online');
  assert.match(online.result.generationId,/^[a-f0-9]{32}$/);
  const before = (await links(page)).links;
  assert.deepEqual(before,[joined.result]);
  await publish(`${own}/fresh-joined.json`,{grantId});
  checkpoints.push('explicit-local-discovery-exact-invitation-and-paired-status-grant');

  stage = 'native Avahi and GUI see explicit withdrawal while listener stays open';
  const stopped = await waitSignal(`${other}/fresh-mdns-stopped.json`);
  await expect.poll(nativeRecords,{timeout:12000}).toEqual([]);
  assert.ok(Date.now()/1000 < stopped.expiresAt);
  const absent = await clickReceipt(page,button(page,'Find nearby Amplifiers'),'lan-browse');
  assert.equal(absent.http,200);
  assert.deepEqual(absent.result,{status:'empty',instances:[]});
  await expect(button(page,'Check availability')).toBeEnabled();
  assert.equal((await check()).result?.availability,'online');
  await publish(`${own}/fresh-mdns-withdrawn.json`,{empty:true});
  checkpoints.push('native-avahi-and-owner-browse-empty-after-explicit-withdrawal');

  stage = 'peer is unreachable after confirmed disable and stale conflict';
  assert.equal((await waitSignal(`${other}/fresh-disabled.json`)).listener,'none');
  await refuseTcp(addr.hostname,Number(addr.port));
  const offline = await check();
  assert.equal(offline.http,503);
  assert.equal(offline.code,'link_unavailable');
  assert.equal(offline.result,undefined);
  await expect(page.getByText('Unreachable at last check',{exact:true})).toBeVisible();
  assert.deepEqual((await links(page)).links,before);
  await publish(`${own}/fresh-closed.json`,{peerClosed:true,grantId});
  checkpoints.push('exposed-peer-port-closed-and-joiner-grant-retained');

  stage = 'same pin and grant after source restarts';
  assert.equal((await waitSignal(`${other}/fresh-restarted.json`)).grantId,grantId);
  const verified = await peerProbe(addr.origin,payload.fingerprint,prefix+'peer/identity',{body:args});
  assert.equal(verified.http,200);
  assert.deepEqual(JSON.parse(verified.text),{result:args});
  const after = await check();
  assert.equal(after.http,200);
  assert.equal(after.result.instanceId,payload.instanceId);
  assert.equal(after.result.availability,'online');
  assert.notEqual(after.result.generationId,online.result.generationId);
  assert.deepEqual((await links(page)).links,before);
  await publish(`${own}/fresh-checked.json`,{grantId});
  checkpoints.push('same-tls-pin-same-grant-status-online-after-new-peer-generation');

  stage = 'cross-guest TCP remains closed during local issuer revoke';
  assert.equal((await waitSignal(`${other}/fresh-off-for-revoke.json`)).grantId,grantId);
  await refuseTcp(addr.hostname,Number(addr.port));
  await publish(`${own}/fresh-off-confirmed.json`,{grantId});
  assert.equal((await waitSignal(`${other}/fresh-revoked-off.json`)).grantId,grantId);
  await refuseTcp(addr.hostname,Number(addr.port));
  await publish(`${own}/fresh-revoked-off-confirmed.json`,{grantId});
  stage = 'remote status is denied once the issuer explicitly reopens the same listener';
  assert.equal((await waitSignal(`${other}/fresh-revoked.json`)).grantId,grantId);
  const denied = await check();
  assert.equal(denied.http,403);
  assert.equal(denied.code,'link_revoked');
  assert.equal(denied.result,undefined);
  assert.deepEqual((await links(page)).links,before);
  await expect(page.getByText('Access was revoked or no longer authorized at last check',{exact:true})).toBeVisible();
  await publish(`${own}/fresh-denied.json`,{grantId});
  checkpoints.push('remote-revocation-observed-in-owner-ui');
}

try {
  const host = await wait(`${own}/fresh-ready.json`);
  assert.match(host.base,/^http:\/\/127\.0\.0\.1:\d+$/);
  stage = 'explicit guest Chromium executable';
  assert.equal(process.env.PLAYWRIGHT_BROWSERS_PATH,'/opt/paired/browsers');
  const executablePath = chromium.executablePath();
  assert.ok(executablePath.startsWith('/opt/paired/browsers/'));
  await access(executablePath,constants.X_OK);
  browser = await chromium.launch({headless:true,executablePath});
  result.browserVersion = browser.version();
  const context = await browser.newContext({serviceWorkers:'block',viewport:{width:1400,height:960}});
  await context.route('**/*', route => new URL(route.request().url()).origin===host.base ? route.continue() : route.abort());
  const page = await signedInTab(context,host);
  stage = 'cross-guest main port refusal before owner consent';
  const neighbor = await wait(`${other}/fresh-ready.json`);
  assert.notEqual(host.address,neighbor.address);
  await refuseTcp(neighbor.address,neighbor.mainPort);
  await publish(`${own}/fresh-main-private.json`,{refused:true});
  assert.equal((await waitSignal(`${other}/fresh-main-private.json`)).refused,true);
  checkpoints.push('cross-guest-main-port-refused-before-owner-consent');
  stage = 'local owner consents without exposing main app';
  const active = await enable(page,host);
  if (role==='source') await source(page,context,host,active);
  else await destination(page,host);
  result.checkPassed = true;
} catch (error) {
  result.error = {name:error.name,code:error.code};
  process.exitCode = 1;
} finally {
  try { await browser?.close(); }
  finally {
    if (role==='source') {
      await unlink(invitationFile).catch(error => {if (error.code!=='ENOENT') throw error;});
    }
    await publish(`${own}/fresh-result.json`,{...result,stage});
  }
}