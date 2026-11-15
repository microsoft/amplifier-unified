// Playwright's serviceWorkers:'block' initializer dereferences
// navigator.serviceWorker in every frame. Opaque sandboxed previews throw before
// the app executes. Keep registration disabled without generating that unrelated
// instrumentation error; do not filter pageerror events from the app.
export async function fenceLayoutServiceWorkers(context) {
  await context.addInitScript(() => {
    let workers;
    try { workers = navigator.serviceWorker; }
    catch (error) {
      if (error.name !== 'SecurityError') throw error;
      // The browser itself forbids workers in this opaque-origin frame.
      return;
    }
    if (workers) workers.register = async () => {
      throw new DOMException('Service-worker registration disabled by the isolated layout audit', 'NotAllowedError');
    };
  });
}
