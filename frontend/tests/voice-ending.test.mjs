import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from '../src/voice.js';
import {VoicePlayback} from '../src/voice-playback.js';

globalThis.window = {addEventListener() {}, removeEventListener() {}};
const deferred = () => {let resolve; const promise = new Promise(done => {resolve = done}); return {promise, resolve};};
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function fixture(request = async () => ({finalized: true, maxDrainMs: 1000})) {
  const calls = [], released = [];
  const client = new VoiceClient({request: (...args) => {calls.push(args); return request(...args);}});
  client.state = {status: 'connected', id: 'call_1', provider: 'realtime'};
  client.audio = {srcObject: {}, pause() {released.push('audio');}};
  client.peer = {connectionState: 'connected', close() {released.push('peer');}};
  const track = {enabled: true, stop() {released.push('mic');}};
  client.stream = {getAudioTracks: () => [track], getTracks: () => [track]};
  return {client, calls, released, track};
}

test('agent end retains playback until the drain completes, after preparation', async () => {
  const ready = deferred(), drain = deferred();
  const f = fixture((path, options) => options.body.graceful ? ready.promise : Promise.resolve({finalized: true}));
  let draining = false;
  f.client.playback.drain = () => {draining = true; return drain.promise;};
  const ending = f.client.end({id: 'call_1', graceful: true});
  assert.equal(f.track.enabled, false);
  assert.deepEqual(f.released, []);
  assert.equal(draining, false);
  ready.resolve({maxDrainMs: 5000}); await tick();
  assert.equal(draining, true); assert.deepEqual(f.released, []);
  drain.resolve('buffer-drained'); await ending;
  assert.deepEqual(f.released, ['mic', 'peer', 'audio']);
  assert.deepEqual(f.calls.map(([, args]) => args.body), [{id: 'call_1', graceful: true}, {id: 'call_1'}]);
  assert.equal(f.client.state.finalized, true);
});

test('user hangup releases audio immediately while finalization is pending', async () => {
  const finish = deferred(), f = fixture(() => finish.promise);
  const ending = f.client.end();
  assert.deepEqual(f.released, ['mic', 'peer', 'audio']);
  finish.resolve({finalized: false}); await ending;
  assert.equal(f.client.state.finalized, false);
});

test('user hangup preempts graceful preparation; late prepare cannot close a new call', async () => {
  const ready = deferred();
  const f = fixture((path, options) => options.body.graceful ? ready.promise : Promise.resolve({finalized: true}));
  const graceful = f.client.end({graceful: true});
  await f.client.end();
  f.client.state = {status: 'connected', id: 'call_2'};
  ready.resolve({maxDrainMs: 2000}); await graceful;
  assert.equal(f.client.state.id, 'call_2'); assert.equal(f.calls.length, 2);
});

test('unrelated browser and stale effect never end another call', async () => {
  const f = fixture();
  await f.client.end({id: 'old_call', graceful: true});
  assert.equal(f.calls.length, 0); assert.deepEqual(f.released, []);
  f.client.release(); f.client.state = {status: 'idle'};
  await f.client.end({id: 'call_1'});
  assert.equal(f.calls.length, 0);
});

test('duplicate graceful request shares the same drain and interrupted preparation is quiet', async () => {
  const f = fixture((path, options) => options.body.graceful ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(Error('aborted')))) : Promise.resolve({}));
  const one = f.client.end({graceful: true}), two = f.client.end({graceful: true});
  assert.equal(f.calls.length, 1);
  await f.client.end(); await Promise.all([one, two]);
  assert.equal(f.calls.length, 2);
});

test('Realtime response/transcript completion does not mean audio has drained', async () => {
  const playback = new VoicePlayback();
  const signal = new AbortController();
  playback.event({type: 'response.created'});
  playback.event({type: 'output_audio_buffer.started'});
  const pending = playback.drain({provider: 'realtime', peer: {connectionState: 'connected'}, signal: signal.signal, timeoutMs: 1000});
  playback.event({type: 'response.output_audio_transcript.done'});
  playback.event({type: 'response.done'});
  let done = false; pending.then(() => {done = true;});
  await tick(); assert.equal(done, false);
  playback.event({type: 'output_audio_buffer.stopped'});
  assert.equal(await pending, 'buffer-drained');
});

test('missing playback events time out; connection loss and user interruption finish promptly', async () => {
  const playback = new VoicePlayback();
  assert.equal(await playback.drain({provider: 'live', peer: {connectionState: 'connected'}, timeoutMs: 5}), 'timeout');
  assert.equal(await playback.drain({provider: 'realtime', peer: {connectionState: 'failed'}}), 'disconnected');
  const controller = new AbortController();
  const drain = playback.drain({provider: 'live', peer: {connectionState: 'connected'}, signal: controller.signal});
  controller.abort(); assert.equal(await drain, 'interrupted');
});

test('prepare failure still releases media and finalizes the correct call', async () => {
  const f = fixture((path, options) => options.body.graceful ? Promise.reject(Error('network down')) : Promise.resolve({}));
  await assert.rejects(f.client.end({graceful: true}), /network down/);
  assert.deepEqual(f.released, ['mic', 'peer', 'audio']);
  assert.equal(f.client.state.status, 'ended');
});
