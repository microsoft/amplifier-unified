(() => {
  'use strict';
  const id = __CANVAS_ID__;
  const child = document.createElement('iframe');
  child.title = 'Interactive surface content';
  child.sandbox = 'allow-scripts';
  let channel, blocked = false;
  const report = () => {
    if (channel && blocked) parent.postMessage({type: 'canvas-app', id, channel,
      op: 'status', status: 'error', message: 'Navigation outside this surface is blocked. Restore or refine its design to continue.'}, '*');
  };
  addEventListener('securitypolicyviolation', event => {
    if (event.violatedDirective.startsWith('frame-src')) { blocked = true; report(); }
  });
  addEventListener('message', event => {
    const data = event.data;
    if (!data || data.id !== id) return;
    if (event.source === parent && data.type === 'canvas-app-host') {
      channel = data.channel;
      child.contentWindow?.postMessage(data, '*');
      report();
    } else if (event.source === child.contentWindow && data.type === 'canvas-app') {
      parent.postMessage(data, '*');
    }
  });
  child.src = __DOCUMENT_URL__;
  document.body.append(child);
})();
