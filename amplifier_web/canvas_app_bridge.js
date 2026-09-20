(() => {
  'use strict';
  const id = __CANVAS_ID__;
  let snapshot, channel, sequence = 0, editVersion = 0, tail = Promise.resolve();
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
  function operation(op, args) {
    const editing = editVersion;
    const run = tail.then(async () => {
      await ready;
      const requestId = String(++sequence);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId);
          reject(Error('The host did not acknowledge this change. Inspect the surface before retrying.'));
        }, 20000);
        pending.set(requestId, {resolve, reject, timer});
        send({op, args, requestId, editVersion: editing, revision: snapshot.app.revision, stateRevision: snapshot.app.stateRevision});
      });
    });
    tail = run.catch(() => {});
    return run;
  }
  addEventListener('message', event => {
    const data = event.data;
    if (event.source !== parent || !data || data.type !== 'canvas-app-host' || data.id !== id) return;
    if (channel && data.channel !== channel) return;
    const connected = !channel;
    channel = data.channel;
    if (connected && document.readyState !== 'loading') report();
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
      for (const listener of listeners) { try { listener(copy(snapshot)); } catch (error) { console.error(error); } }
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
    patch: patch => operation('state', {patch}),
    emit: (name, payload = {}) => operation('event', {name, payload}),
    request: (name, input = {}) => operation('request', {name, input}),
    setDirty: dirty => operation('dirty', {dirty: !!dirty}),
  }), writable: false});
  // Unsaved form edits keep the old frame alive if a remote revision races an input.
  addEventListener('input', () => send({op: 'editing', editVersion: ++editVersion}), true);
  send({op: 'ready'});
})();
