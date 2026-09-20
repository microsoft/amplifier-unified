/** Actual Chromium MediaStream/Web Audio playback; no provider or microphone use. */
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const server = await createServer({configFile: false, server: {host: '127.0.0.1', port: 0, strictPort: false}, appType: 'custom'});
await server.listen(0);
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
const browser = await chromium.launch({headless: true, args: ['--autoplay-policy=no-user-gesture-required']});
try {
  const page = await browser.newPage();
  await page.goto(origin + '/src/voice-playback.js');
  const result = await page.evaluate(async () => {
    const {VoiceClient} = await import('/src/voice.js');
    const context = new AudioContext(), source = context.createOscillator();
    const destination = context.createMediaStreamDestination();
    source.connect(destination); source.frequency.value = 440; source.start();
    const audio = new Audio(); audio.srcObject = destination.stream; await audio.play();
    const calls = [];
    const client = new VoiceClient({request: async (path, options) => {
      calls.push({graceful: !!options.body.graceful, at: performance.now()});
      return options.body.graceful ? {maxDrainMs: 5000} : {finalized: true};
    }});
    client.state = {id: 'synthetic-live', status: 'connected', provider: 'live'};
    client.audio = audio;
    client.peer = {connectionState: 'connected', close() {this.connectionState = 'closed';}};
    const started = performance.now();
    const ending = client.end({graceful: true});
    await new Promise(resolve => setTimeout(resolve, 500));
    const during = {paused: audio.paused, status: client.state.status, closed: client.peer === null};
    source.stop();
    const stopped = performance.now();
    await ending;
    const result = {during, elapsed: performance.now() - started, afterSound: performance.now() - stopped,
      drain: client.state.playbackDrain, finalized: client.state.finalized, paused: audio.paused,
      calls: calls.map(call => call.graceful)};
    client.dispose(); await context.close();
    return result;
  });
  assert.deepEqual(result.during, {paused: false, status: 'ending', closed: false});
  assert.equal(result.drain, 'media-quiet');
  assert(result.afterSound >= 1100 && result.afterSound < 4000, JSON.stringify(result));
  assert.equal(result.finalized, true); assert.equal(result.paused, true);
  assert.deepEqual(result.calls, [true, false]);
  console.log(JSON.stringify(result));
} finally {
  await browser.close(); await server.close();
}
