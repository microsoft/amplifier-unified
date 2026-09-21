import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Markdown} from '../src/markdown.js';
const render=text=>renderToStaticMarkup(React.createElement(Markdown,{text}));
test('assistant and worker Markdown renders headings, code, lists, and GFM tables',()=>{
 const html=render('# Summary\n\n**Ready** with `code`.\n\n- One\n- Two\n\n| Task | Status |\n|---|---|\n| Build | Done |\n\n```python\nprint("hello")\n```');
 for(const fragment of ['<h1>Summary</h1>','<strong>Ready</strong>','<ul>','<table>','<pre>','language-python'])assert.ok(html.includes(fragment),fragment);
});
test('Markdown blocks raw HTML and unsafe URLs, and secures new-window links',()=>{
 const html=render('<script>alert(1)</script>\n\n[Bad](javascript:alert)\n\n[Good](https://example.com)\n\n<img src=x onerror=alert(1)>');
 assert.ok(!html.includes('<script'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('href="javascript:'));
 assert.match(html,/href="https:\/\/example.com"[^>]*target="_blank"[^>]*rel="noopener noreferrer"/);
});
test('images require an explicit click instead of fetching remote assets',()=>{
 const html=render('![A diagram](https://example.com/diagram.png)');
 assert.ok(!html.includes('<img'));assert.ok(html.includes('Image: A diagram'));assert.ok(html.includes('href="https://example.com/diagram.png"'));
});

test('reusable writing retains safe Markdown and inert metadata',()=>{
 const html=render(':::writing{variant="document" id="12345"}\n**A draft**\n\n<script>alert(1)</script>\n:::');
 assert.ok(html.includes('Reusable writing'));assert.ok(html.includes('<strong>A draft</strong>'));assert.ok(html.includes('Copy writing'));assert.ok(!html.includes('<script>'));
});
