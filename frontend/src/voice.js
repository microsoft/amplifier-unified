import {VoicePlayback} from "./voice-playback.js";

/** Browser media transport. The server owns credentials, delegation, and state. */
export class VoiceClient {
  constructor({request, onState = () => {}, onError = () => {}}) {
    this.request = request;
    this.onState = onState;
    this.onError = onError;
    this.state = {status: 'idle', muted: false};
    this.generation = 0;
    this.peer = null;
    this.stream = null;
    this.events = null;
    this.audio = null;
    this.readySent = false;
    this.playback = new VoicePlayback();
    this.ending = null;
    this.onPageHide = () => {
      if (this.state.id) this.request('/api/voice/end', {method: 'POST', body: {id: this.state.id}, keepalive: true}).catch(() => {});
      this.release();
    };
    window.addEventListener('pagehide', this.onPageHide);
  }

  update(patch) {
    this.state = {...this.state, ...patch};
    this.onState({...this.state});
  }

  async start({provider = 'auto', sessionId = null} = {}) {
    if (['connecting', 'connected', 'ending'].includes(this.state.status)) return this.state;
    if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
      throw new Error('Voice needs a browser with microphone and WebRTC support, on localhost or HTTPS.');
    }
    const generation = ++this.generation;
    this.playback = new VoicePlayback();
    this.update({status: 'connecting', error: null, muted: false});
    try {
      const config = await this.request('/api/voice/config');
      if (!config.available) throw new Error(config.reason || 'Voice is not configured on the host.');
      const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      if (generation !== this.generation) {
        stream.getTracks().forEach(track => track.stop());
        return this.state;
      }
      this.stream = stream;
      const peer = this.peer = new RTCPeerConnection();
      this.audio = new Audio();
      this.audio.autoplay = true;
      this.audio.setAttribute('playsinline', '');
      peer.addEventListener('track', event => {
        if (!this.audio) return;
        this.audio.srcObject = event.streams[0] || new MediaStream([event.track]);
        this.audio.play().catch(() => this.onError(new Error('Your browser blocked voice playback. Use its site audio controls, then reconnect.')));
      });
      stream.getTracks().forEach(track => peer.addTrack(track, stream));
      const channel = this.events = peer.createDataChannel('oai-events');
      this.readySent = false;
      const ready = async () => {
        if (this.readySent || !this.state.id || peer.connectionState !== 'connected' || channel.readyState !== 'open') return;
        this.readySent = true;
        try {
          await this.request('/api/voice/ready', {method: 'POST', body: {id: this.state.id}});
          if (generation === this.generation) this.update({status: 'connected'});
        } catch (error) {
          this.onError(error);
          await this.end();
        }
      };
      channel.addEventListener('open', ready);
      channel.addEventListener('message', event => {
        // Sideband records authoritative transcript; never execute browser events as tools.
        try {
          const data = JSON.parse(event.data);
          if (generation !== this.generation) return;
          this.playback.event(data);
          if (data.type === 'session.closed' && generation === this.generation) {
            this.release();
            this.update({status: 'ended', finalized: true, id: null});
          }
        } catch (_) { /* Ignore malformed provider captions. */ }
      });
      peer.addEventListener('connectionstatechange', () => {
        ready();
        if (peer.connectionState === 'failed' && generation === this.generation) {
          this.onError(new Error('The voice connection failed. Your background work continues.'));
          this.end().catch(this.onError);
        }
      });
      await peer.setLocalDescription(await peer.createOffer());
      const answer = await this.request('/api/voice/connect', {method: 'POST', body: {sdp: peer.localDescription.sdp, provider, sessionId}});
      if (generation !== this.generation) {
        await this.request('/api/voice/end', {method: 'POST', body: {id: answer.id}});
        return this.state;
      }
      this.update({id: answer.id, provider: answer.provider, model: answer.model, fallbackReason: answer.fallbackReason || null});
      await peer.setRemoteDescription({type: 'answer', sdp: answer.sdp});
      await ready();
      this.connectionTimer = setTimeout(() => {
        if (generation === this.generation && this.state.status === 'connecting') {
          this.onError(new Error('Voice media could not connect. Check microphone and network access, then try again.'));
          this.end().catch(this.onError);
        }
      }, 25000);
      return this.state;
    } catch (error) {
      if (generation !== this.generation) return this.state;
      if (this.state.id) {
        try { await this.request('/api/voice/end', {method: 'POST', body: {id: this.state.id}}); } catch (_) {}
      }
      this.release();
      const explanation = error.name === 'NotAllowedError' ? 'Microphone access was declined. Allow microphone access for this site, then call again.' : error.message;
      this.update({status: 'error', error: explanation, id: null});
      throw new Error(explanation);
    }
  }

  setMuted(muted) {
    const value = this.state.status === 'ending' || Boolean(muted);
    this.stream?.getAudioTracks().forEach(track => { track.enabled = !value; });
    this.update({muted: value});
  }

  async end({id = this.state.id, graceful = false} = {}) {
    // Effects are broadcast. Only the browser holding this exact call drains it.
    if (id && id !== this.state.id) return;
    if (!this.state.id && !this.peer && this.state.status !== 'connecting') return;
    if (graceful && this.ending) return this.ending.promise;
    const identity = this.state.id;
    if (graceful && identity && this.peer) {
      const controller = new AbortController();
      const ending = {controller, promise: null, timer: null};
      this.ending = ending;
      ending.timer = setTimeout(() => this.end({id: identity}).catch(this.onError), 20000);
      const since = performance.now();
      this.update({status: 'ending', muted: true});
      this.stream?.getAudioTracks().forEach(track => { track.enabled = false; });
      ending.promise = (async () => {
        try {
          const ready = await this.request('/api/voice/end', {method: 'POST', body: {id: identity, graceful: true}, signal: controller.signal});
          if (controller.signal.aborted || this.state.id !== identity) return;
          if (!ready.closed) {
            const drain = await this.playback.drain({provider: this.state.provider, audio: this.audio, peer: this.peer, signal: controller.signal, since, timeoutMs: ready.maxDrainMs});
            if (!controller.signal.aborted) this.update({playbackDrain: drain});
          }
        } catch (error) {
          if (!controller.signal.aborted) throw error;
        } finally {
          if (!controller.signal.aborted && this.state.id === identity) await this.end({id: identity});
        }
      })();
      return ending.promise;
    }
    const generation = ++this.generation;
    this.update({status: 'ending'});
    // A user stop releases microphone and speaker immediately, even while the
    // provider takes time to finalize transcript/usage or its request fails.
    this.release();
    let result;
    try {
      result = await this.request('/api/voice/end', {method: 'POST', body: {id: identity || null}});
      return result;
    } finally {
      if (generation === this.generation) this.update({status: 'ended', finalized: result?.finalized || false, id: null});
    }
  }

  release() {
    clearTimeout(this.ending?.timer);
    this.ending?.controller.abort();
    this.ending = null;
    clearTimeout(this.connectionTimer);
    this.stream?.getTracks().forEach(track => track.stop());
    this.events?.close();
    this.peer?.close();
    if (this.audio) { this.audio.pause(); this.audio.srcObject = null; }
    this.peer = this.stream = this.events = this.audio = null;
  }

  dispose() {
    ++this.generation;
    this.onPageHide();
    window.removeEventListener('pagehide', this.onPageHide);
  }
}
