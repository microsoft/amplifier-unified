/** Playback evidence, never inferred from transcript text or response completion. */
export class VoicePlayback {
  constructor() {
    this.responding = false;
    this.playing = false;
    this.stoppedAt = null;
  }

  event(event) {
    if (event.type === 'response.created') { this.responding = true; this.stoppedAt = null; }
    if (event.type === 'response.done') this.responding = false;
    if (event.type === 'output_audio_buffer.started') { this.playing = true; this.stoppedAt = null; }
    if (['output_audio_buffer.stopped', 'output_audio_buffer.cleared'].includes(event.type)) {
      this.playing = false;
      this.stoppedAt = performance.now();
    }
  }

  async drain({provider, audio, peer, signal, since = performance.now(), timeoutMs = 12000}) {
    let context, source, analyser, samples;
    // Live has no output-done event. Observe decoded media quiet as a bounded,
    // best-effort fallback; neither transcript deltas nor session.closed prove
    // the listener heard a complete utterance.
    if (provider === 'live' && audio?.srcObject) {
      try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        context = new AudioContext();
        source = context.createMediaStreamSource(audio.srcObject);
        analyser = context.createAnalyser();
        analyser.fftSize = 2048;
        source.connect(analyser);
        samples = new Float32Array(analyser.fftSize);
        // Do not await resume: autoplay restrictions must not defeat the timeout.
        context.resume().catch(() => {});
      } catch (_) { /* No media evidence: retain the bounded timeout. */ }
    }
    try {
      return await new Promise(resolve => {
        let lastSound = null, timer, deadline;
        const finish = reason => {
          clearInterval(timer); clearTimeout(deadline);
          signal?.removeEventListener('abort', aborted);
          resolve(reason);
        };
        const aborted = () => finish('interrupted');
        const check = () => {
          if (signal?.aborted) return finish('interrupted');
          if (!peer || ['failed', 'closed', 'disconnected'].includes(peer.connectionState)) return finish('disconnected');
          const now = performance.now();
          if (provider === 'realtime' && !this.responding && !this.playing && this.stoppedAt !== null && this.stoppedAt >= since && now - this.stoppedAt >= 300) return finish('buffer-drained');
          if (analyser && context.state === 'running') {
            analyser.getFloatTimeDomainData(samples);
            const power = samples.reduce((sum, value) => sum + value * value, 0) / samples.length;
            if (power > 0.00001) lastSound = now;
            if (lastSound !== null && now - lastSound >= 1200 + (context.outputLatency || 0) * 1000) return finish('media-quiet');
          }
        };
        timer = setInterval(check, 50);
        deadline = setTimeout(() => finish('timeout'), Math.max(0, Math.min(timeoutMs, 12000)));
        signal?.addEventListener('abort', aborted, {once: true});
        check();
      });
    } finally {
      source?.disconnect();
      if (context) context.close().catch(() => {});
    }
  }
}
