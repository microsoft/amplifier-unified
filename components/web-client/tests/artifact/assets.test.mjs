import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
const root=fileURLToPath(new URL('../../dist/',import.meta.url));
test('packaged page loads its real stylesheet without JavaScript boot',()=>{
 const html=readFileSync(join(root,'index.html'),'utf8');
 const link=html.match(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>/);
 assert.ok(link,'stylesheet link is present in HTML');assert.ok(!link[0].includes('media="not all"'));
 const css=readFileSync(join(root,link[1]),'utf8');assert.ok(css.includes('#amp-one'));assert.ok(css.includes('.a-layout'));assert.ok(css.includes('--a-background-art'));assert.ok(!css.includes('@scope'));
 for(const [,path] of html.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g))assert.ok(existsSync(join(root,path)),`bundled ${path} exists`);
});
test('packaged Reference CSS matches the shared appearance foundation',()=>{
 const css=readFileSync(join(root,'reference.amplifier.css'),'utf8');
 assert.equal(css,readFileSync(new URL('../../src/reference.css',import.meta.url),'utf8'));
 assert.ok(css.includes('--a-font-heading'));assert.ok(css.includes('--a-background-art'));
 assert.ok(css.includes('.a-dialog'));assert.ok(css.includes('.a-layout'));
 for(const selector of ['.a-message-delivery','.a-delivery-confirm','.a-delivery-content'])assert.ok(css.includes(selector),`packaged delivery recovery retains ${selector}`);
 assert.ok(!/@import\b/.test(css));
});
