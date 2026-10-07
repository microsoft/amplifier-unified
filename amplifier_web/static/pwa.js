// Installation is offered by the browser. Registration never reloads an active chat.
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', {scope: '/', updateViaCache: 'none'})
      .catch(() => { /* The online app continues to work if offline support is unavailable. */ });
  }, {once: true});
}
