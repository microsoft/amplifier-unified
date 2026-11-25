// Focused harness regression, no app/fixture/provider required.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {chromium} from '@playwright/test';
import {fenceLayoutServiceWorkers} from './layout-pass2-browser-context.mjs';
const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
const rows = [];
try {
  for (const mode of ['stock-block', 'guarded-audit-fence']) {
    const context = await browser.newContext({serviceWorkers:mode === 'stock-block' ? 'block':'allow'});
    if (mode === 'guarded-audit-fence') await fenceLayoutServiceWorkers(context);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push({name:error.name,message:error.message,stack:error.stack}));
    await page.route('http://127.0.0.1:31337/**', route => route.fulfill({
      contentType:'text/html',
      body:'<iframe sandbox="allow-scripts" srcdoc="<p>Opaque preview</p>"></iframe>',
    }));
    await page.goto('http://127.0.0.1:31337/synthetic-layout-probe');
    await page.frames()[1].waitForSelector('p');
    // Ensure initializer events have crossed the browser protocol before reading.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    rows.push({mode, errors, workers:context.serviceWorkers().length});
    if (mode === 'stock-block') {
      assert.equal(errors.length,1);
      assert.equal(errors[0].name,'SecurityError');
      assert.match(errors[0].message,/serviceWorker.*sandboxed/);
    } else {
      assert.deepEqual(errors,[]);
      assert.equal(context.serviceWorkers().length,0);
      // An arbitrary application script error still arrives; it is not filtered.
      await Promise.all([page.waitForEvent('pageerror'),
        page.evaluate(() => {setTimeout(() => {throw Error('application-error-sentinel');},0);})]);
      assert.equal(errors.length,1);
      assert.equal(errors[0].message,'application-error-sentinel');
      rows.at(-1).applicationSentinelObserved = true;
    }
    await context.close();
  }
} finally {
  await browser.close();
  if (process.env.AMPLIFIER_TEST_ARTIFACTS)
    await writeFile(process.env.AMPLIFIER_TEST_ARTIFACTS,JSON.stringify(rows,null,2)+'\n');
}
console.log('Harness service-worker fence: opaque-frame guard and unfiltered application error verified.');
