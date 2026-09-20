(() => {
  'use strict';
  const id = __CANVAS_ID__;
  let snapshot, channel, sequence = 0, editVersion = 0, queued = 0, notified, tail = Promise.resolve();
  const listeners = new Set(), pending = new Map();
  let readyResolve;
  const ready = new Promise(resolve => { readyResolve = resolve; });
  const send = value => parent.postMessage({type: 'canvas-app', id, channel, ...value}, '*');
  const copy = value => structuredClone(value);
  let renderError = '';
  const report = () => { if (channel) send({op: 'status', status: renderError ? 'error' : 'ready', message: renderError || 'Sandbox document loaded'}); };
  addEventListener('error', event => { renderError = String(event.message || 'Surface script failed').slice(0, 1000); report(); });
  addEventListener('unhandledrejection', event => { renderError = String(event.reason?.message || 'Surface interaction failed').slice(0, 1000); report(); });
  addEventListener('DOMContentLoaded', report, {once: true});
  const markEdit = () => {
    ++editVersion;
    if (channel) send({op: 'editing', editVersion});
    return editVersion;
  };
  function notify() {
    if (!snapshot || queued) return;
    const key = JSON.stringify([snapshot.app.revision, snapshot.app.stateRevision, snapshot.theme]);
    if (key === notified) return;
    notified = key;
    for (const listener of listeners) { try { listener(copy(snapshot)); } catch (error) { console.error(error); } }
  }
  function operation(op, args, options = {}) {
    const editing = editVersion, commit = options.commit;
    if (commit !== undefined && (!Number.isSafeInteger(commit) || commit < 0 || commit > editing))
      return Promise.reject(Error('Commit must identify an existing local edit.'));
    args = copy(args);
    queued++;
    const run = tail.then(async () => {
      await ready;
      const requestId = String(++sequence);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId);
          reject(Error('The host did not acknowledge this change. Inspect the surface before retrying.'));
        }, 20000);
        pending.set(requestId, {resolve, reject, timer});
        send({op, args, requestId, editVersion: editing, commit, revision: snapshot.app.revision, stateRevision: snapshot.app.stateRevision});
      });
    });
    const finished = run.finally(() => { queued--; notify(); });
    tail = finished.catch(() => {});
    return finished;
  }
  function observeCanvas(canvas, draw) {
    const context = canvas.getContext('2d');
    if (!context || typeof draw !== 'function') throw Error('Provide a 2D canvas and a drawing callback.');
    let stopped = false;
    const redraw = () => {
      if (stopped) return false;
      const {width, height} = canvas.getBoundingClientRect();
      if (!(width > 0 && height > 0)) return false;
      const dpr = window.devicePixelRatio || 1;
      const pixels = [Math.max(1, Math.round(width * dpr)), Math.max(1, Math.round(height * dpr))];
      if (canvas.width !== pixels[0]) canvas.width = pixels[0];
      if (canvas.height !== pixels[1]) canvas.height = pixels[1];
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      try { draw({context, width, height, dpr}); }
      catch (error) { renderError = String(error?.message || 'Canvas drawing failed').slice(0, 1000); report(); }
      return true;
    };
    const observer = new ResizeObserver(redraw);
    observer.observe(canvas);
    addEventListener('resize', redraw);
    redraw();
    return Object.freeze({redraw, disconnect: () => { stopped = true; observer.disconnect(); removeEventListener('resize', redraw); }});
  }
  addEventListener('message', event => {
    const data = event.data;
    if (event.source !== parent || !data || data.type !== 'canvas-app-host' || data.id !== id) return;
    if (channel && data.channel !== channel) return;
    const connected = !channel;
    channel = data.channel;
    if (connected) {
      if (editVersion) send({op: 'editing', editVersion});
      if (document.readyState !== 'loading') report();
    }
    if (data.snapshot && (!snapshot || data.snapshot.app.stateRevision >= snapshot.app.stateRevision)) {
      snapshot = data.snapshot;
      const theme = snapshot.theme;
      if (theme && document.documentElement) {
        const mode = snapshot.app.manifest.theme || 'inherit';
        for (const [name, value] of Object.entries(theme.tokens)) {
          const key = '--host-' + name;
          if (mode === 'inherit' || mode === 'accent-only' && name === 'accent') document.documentElement.style.setProperty(key, value);
          else document.documentElement.style.removeProperty(key);
        }
        document.documentElement.dataset.hostScheme = theme.scheme;
      }
      readyResolve(copy(snapshot));
      notify();
    }
    const request = pending.get(data.requestId);
    if (request) {
      clearTimeout(request.timer); pending.delete(data.requestId);
      data.error ? request.reject(Error(data.error)) : request.resolve(copy(data.snapshot));
    }
  });
  Object.defineProperty(window, 'canvasApp', {value: Object.freeze({
    ready,
    getSnapshot: () => snapshot ? copy(snapshot) : null,
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    patch: (patch, options) => operation('state', {patch}, options),
    emit: (name, payload = {}, options) => operation('event', {name, payload}, options),
    request: (name, input = {}) => operation('request', {name, input}),
    beginEdit: markEdit,
    getEditVersion: () => editVersion,
    setDirty: dirty => { if (dirty) markEdit(); return operation('dirty', {dirty: !!dirty}); },
    reportError: error => { renderError = String(error?.message || error || 'Surface script failed').slice(0, 1000); report(); },
    reportReady: () => { renderError = ''; report(); },
    observeCanvas,
  }), writable: false});
  // Unsaved form edits keep the old frame alive if a remote revision races an input.
  addEventListener('input', markEdit, true);
  send({op: 'ready'});
})();
