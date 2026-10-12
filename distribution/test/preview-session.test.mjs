import test from 'node:test';
import assert from 'node:assert/strict';
import {previewSessions, PREVIEW_SESSION_MS} from '../src/preview-session.mjs';

const code = 'fixture-only-'.repeat(5), origin = 'https://preview.example:24444', now = 1791805800000;
test('preview sign-in survives a new owner instance with its original fixed expiry', () => {
 const first = previewSessions(code, origin), token = first.issue(now);
 const restarted = previewSessions(code, origin);
 assert.equal(restarted.accepts(token, now + 60000), true);
 assert.equal(restarted.accepts(token, now + PREVIEW_SESSION_MS - 1), true);
 assert.equal(restarted.accepts(token, now + PREVIEW_SESSION_MS), false);
 assert.equal(restarted.accepts(token, now - 1), false);
 assert.notEqual(first.issue(now), token);
});
test('preview sessions are scoped to exact access code and origin and reject tampering', () => {
 const sessions = previewSessions(code, origin), token = sessions.issue(now), parts = token.split('.');
 assert.equal(previewSessions(code + 'changed', origin).accepts(token, now), false);
 assert.equal(previewSessions(code, 'https://another.example:24444').accepts(token, now), false);
 assert.equal(previewSessions(code, 'https://preview.example:24445').accepts(token, now), false);
 for (const value of [undefined, '', 'x'.repeat(43), token + 'x', token.replace('v1.', 'v2.'),
  `v1.${Number(parts[1]) - 1}.${parts[2]}.${parts[3]}`,
  `v1.${parts[1]}.${'A'.repeat(43)}.${parts[3]}`,
  `v1.${parts[1]}.${parts[2]}.${'A'.repeat(43)}`]) assert.equal(sessions.accepts(value, now), false);
});
