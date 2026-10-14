/* Evaluate this whole file using the existing Edge tab's AppleScript execute.
 * It installs a recorder in the AppleScript isolated world, without using the
 * app's globals, making network requests, or injecting main-world scripts.
 * Installation does not click anything. Root owns all live use.
 *
 * Subsequent execute calls (return strings explicitly for AppleScript):
 *   JSON.stringify(window.__unifiedDomTiming.inventory())
 *   JSON.stringify(window.__unifiedDomTiming.start({id:'exact-session-id',expectWork:true}))
 *   JSON.stringify(window.__unifiedDomTiming.start({title:'Unified: AHP / ACP',expectWork:true}))
 *   JSON.stringify(window.__unifiedDomTiming.report())
 *   JSON.stringify(window.__unifiedDomTiming.stop())
 *   window.__unifiedDomTiming.dispose(); 'disposed'
 * Pass scope:'.a-work-browser' to specifically exercise workspace-list clicks.
 * minWorkHeaders is an explicit rendered-header minimum, not a history total.
 * An absent/ambiguous target fails before clicking. No message is submitted.
 * Timings are DOM observations, not proof of server completion or exact pixels.
 */
(() => {
  const key = '__unifiedDomTiming';
  window[key]?.dispose?.();
  let run = null, timer = null, resourceObserver = null, longTaskObserver = null;
  const rounded = value => Math.round(value * 100) / 100;
  const trim = value => String(value || '').trim().slice(0, 240);
  const shown = element => !!element && !element.closest('[hidden],[inert]') &&
    element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
  const navButtons = scope => [...(scope || document).querySelectorAll(
    '[data-session-id] button.a-nav-chat-select[data-action="session.select"]')].filter(shown);
  const identity = button => button.closest('[data-session-id]')?.getAttribute('data-session-id');
  const label = button => trim(button.getAttribute('aria-label'));
  const inventory = () => navButtons().slice(0, 100).map(button => ({
    id: identity(button), title: label(button), selected: button.getAttribute('aria-current') === 'page',
    location: button.closest('.a-work-browser') ? 'workspace-list' : 'sidebar', disabled: button.disabled
  }));
  const snapshot = () => {
    const app = document.getElementById('amp-one');
    const pane = app?.querySelector('[data-part="messages"][role="log"]') ||
      app?.querySelector('.a-messages');
    const messages = pane ? [...pane.querySelectorAll('article.a-message[data-message-id]')] : [];
    const header = app?.querySelector('.a-work-heading strong');
    const selected = [...new Set(navButtons().filter(button =>
      button.getAttribute('aria-current') === 'page').map(identity))];
    const groupSelector = '[data-part="execution-group"],.a-work-group';
    const workGroups = [...(pane?.querySelectorAll(groupSelector) || [])]
      .filter(row => !row.parentElement?.closest(groupSelector)).length;
    // Embedded TurnTimeline sections inside an expanded group have no separate
    // header. Counting them would make simply expanding Work look like new work.
    const workSingleTurns = [...(pane?.querySelectorAll('[data-part="execution"]') || [])]
      .filter(row => !row.closest(groupSelector)).length;
    const workHeaders = workGroups + workSingleTurns;
    const composer = app?.querySelector('[role="textbox"][aria-label="Message Amplifier"]');
    return {
      title: document.title, header: trim(header?.getAttribute('title') || header?.textContent),
      surface: app?.getAttribute('data-work-surface') || null, selected,
      paneVisible: shown(pane), busy: pane?.getAttribute('aria-busy') || null,
      draftLength: composer ? (composer.value ?? composer.textContent ?? '').length : null,
      unsavedCanvas: !!app?.querySelector('.a-canvas-unsaved'),
      primaryCanvasPresent: !!app?.querySelector('[data-canvas-view="primary"]'),
      messages: messages.length, firstMessage: messages[0]?.getAttribute('data-message-id') || null,
      lastMessage: messages.at(-1)?.getAttribute('data-message-id') || null, workGroups, workSingleTurns, workHeaders,
      historyError: !!app?.querySelector('.a-session-history-error'),
      visibleAlerts: [...(app?.querySelectorAll('[role="alert"]') || [])].filter(shown).length,
      hidden: document.hidden, readyState: document.readyState
    };
  };
  const append = (list, value, limit = 200) => {
    if (list.length < limit) list.push(value);
    else if (run) run.truncated = true;
  };
  const resource = entry => {
    if (!run || entry.startTime < run.started) return;
    let url;
    try { url = new URL(entry.name, location.href); } catch { return; }
    if (url.origin !== location.origin || !/^\/(api\/|build\.json|assets\/)/.test(url.pathname)) return;
    append(run.requests, {
      path: url.pathname, startMs: rounded(entry.startTime - run.started),
      durationMs: rounded(entry.duration),
      ttfbMs: entry.responseStart && entry.requestStart ? rounded(entry.responseStart - entry.requestStart) : null,
      downloadMs: entry.responseStart ? rounded(entry.responseEnd - entry.responseStart) : null,
      bytes: entry.transferSize, initiator: entry.initiatorType,
      status: entry.responseStatus || null,
      serverTiming: (entry.serverTiming || []).map(row => ({name: row.name, durationMs: row.duration}))
    });
  };
  const finish = reason => {
    if (!run || run.done) return;
    clearTimeout(timer);
    for (const entry of resourceObserver?.takeRecords?.() || []) resource(entry);
    for (const entry of longTaskObserver?.takeRecords?.() || []) {
      append(run.longTasks, {startMs: rounded(entry.startTime - run.started), durationMs: rounded(entry.duration)}, 100);
    }
    resourceObserver?.disconnect(); longTaskObserver?.disconnect();
    run.done = true; run.reason = reason; run.elapsedMs = rounded(performance.now() - run.started);
    run.final = snapshot();
  };
  const sample = () => {
    if (!run || run.done) return;
    const now = performance.now(), elapsed = rounded(now - run.started), state = snapshot();
    const signature = JSON.stringify(state);
    if (signature !== run.lastSignature) {
      append(run.changes, {ms: elapsed, ...state});
      run.lastSignature = signature; run.changedAt = now;
    }
    const mark = (name, condition) => {
      if (condition && run.marks[name] == null) run.marks[name] = elapsed;
    };
    const selected = state.selected.includes(run.target.id);
    const headerMatches = state.header === run.target.title;
    const chat = state.surface === 'chat' && state.paneVisible;
    mark('selectionMs', selected);
    mark('headerMs', headerMatches);
    mark('busyMs', selected && state.busy === 'true');
    const changedMessages = state.firstMessage !== run.before.firstMessage || state.lastMessage !== run.before.lastMessage;
    const newContent = changedMessages || run.marks.busyMs != null;
    mark('messagesMs', selected && chat && newContent && state.messages >= run.minMessages);
    mark('firstWorkHeaderMs', selected && chat && newContent && state.workHeaders > 0);
    mark('minimumWorkHeadersMs', selected && chat && newContent && state.workHeaders >= run.minWorkHeaders);
    const ready = selected && headerMatches && chat && newContent && state.busy === 'false' &&
      state.messages >= run.minMessages && state.workHeaders >= run.minWorkHeaders && !state.historyError;
    mark('readyCandidateMs', ready);
    if (run.lastSample != null && now - run.lastSample > 250) {
      append(run.sampleDelays, {ms: elapsed, delayMs: rounded(now - run.lastSample - 100), hidden: state.hidden}, 100);
    }
    run.lastSample = now;
    if (ready && elapsed >= run.minimumObservationMs && now - run.changedAt >= run.settleMs) return finish('ready-observed');
    if (elapsed >= run.timeoutMs) return finish('timeout');
    timer = setTimeout(sample, 100);
  };
  const report = () => {
    if (!run) return {installed: true, running: false, current: snapshot()};
    const {lastSignature, changedAt, lastSample, started, ...result} = run;
    return {...result, elapsedMs: run.done ? run.elapsedMs : rounded(performance.now() - started), current: snapshot()};
  };
  const start = (options = {}) => {
    if (run && !run.done) return {error: 'A recording is already running; stop it explicitly first.'};
    if (Object.hasOwn(options, 'minWork')) return {error: 'Use minWorkHeaders with the current grouped + single-turn observation semantics.'};
    if (!options.id && !options.title) return {error: 'Supply an exact session id or exact visible chat title.'};
    const scope = options.scope ? document.querySelector(options.scope) : document;
    if (!scope) return {error: 'The requested DOM scope is absent.'};
    const matches = navButtons(scope).filter(button => options.id ?
      identity(button) === options.id : label(button) === options.title);
    const ids = [...new Set(matches.map(identity))];
    if (ids.length !== 1) return {error: ids.length ? 'Ambiguous chat title; use exact id.' : 'Target is not visible in requested scope.', matches: ids};
    const button = matches.find(button => !button.disabled);
    if (!button) return {error: 'Target control is disabled.'};
    const before = snapshot();
    if (before.draftLength !== 0 || before.unsavedCanvas) return {error: 'Nonempty or unknown draft, or unsaved Canvas: no navigation performed.', draftLength:before.draftLength,unsavedCanvas:before.unsavedCanvas};
    if (before.surface === 'chat' && before.selected.includes(ids[0])) return {error: 'Target chat is already selected; choose a different chat.'};
    run = {
      schemaVersion: 4, started: performance.now(), capturedAt: new Date().toISOString(),
      timeOrigin: performance.timeOrigin, target: {id: ids[0], title: label(button)},
      location: button.closest('.a-work-browser') ? 'workspace-list' : 'sidebar',
      timeoutMs: Math.min(180000, Math.max(2000, Number(options.timeoutMs) || 60000)),
      settleMs: Math.min(10000, Math.max(300, Number(options.settleMs) || 1500)),
      minMessages: Number.isInteger(options.minMessages) ? Math.max(0, options.minMessages) : 1,
      minWorkHeaders: Number.isInteger(options.minWorkHeaders) ? Math.max(0, options.minWorkHeaders) : options.expectWork === true ? 1 : 0,
      minimumObservationMs: Math.min(120000, Math.max(0, Number(options.minimumObservationMs) || 0)),
      expectWork: options.expectWork === true, before, changes: [], marks: {}, requests: [],
      longTasks: [], sampleDelays: [], done: false, truncated: false,
      limitations: [
        'Selection/header can change optimistically; message/work readiness is reported separately.',
        'No conversation text, draft content, request bodies, cookies, or URL queries are collected.',
        'Resource timing includes completed requests only; absent entries do not establish no pending requests.',
        'Main-thread delays include hidden-tab throttling and host suspension; they do not identify the cause.',
        'Ready-observed means expected DOM content settled, not verified server quiescence.',
        'Work counts are rendered top-level headers (grouped and single-turn), not canonical turns, events or complete history. Regrouping can change counts without losing work.'
      ]
    };
    try {
      resourceObserver = new PerformanceObserver(list => list.getEntries().forEach(resource));
      resourceObserver.observe({type: 'resource', buffered: false});
    } catch { run.resourceObserverUnavailable = true; }
    try {
      longTaskObserver = new PerformanceObserver(list => list.getEntries().forEach(entry => {
        if (run && !run.done && entry.startTime >= run.started) append(run.longTasks, {
          startMs: rounded(entry.startTime - run.started), durationMs: rounded(entry.duration)
        }, 100);
      }));
      longTaskObserver.observe({type: 'longtask', buffered: false});
    } catch { run.longTaskObserverUnavailable = true; }
    run.changedAt = run.started;
    append(run.changes, {ms: 0, ...before});
    button.click();
    timer = setTimeout(sample, 0);
    return {started: true, target: run.target, timeoutMs: run.timeoutMs, timeOrigin: run.timeOrigin};
  };
  window[key] = Object.freeze({
    inventory, start, report,
    stop: () => { finish('stopped'); return report(); },
    dispose: () => { finish('disposed'); clearTimeout(timer); resourceObserver?.disconnect(); longTaskObserver?.disconnect(); delete window[key]; }
  });
  return JSON.stringify({installed: true, key, timeOrigin: performance.timeOrigin, current: snapshot()});
})();
