import {createHmac, randomBytes, timingSafeEqual} from 'node:crypto';

export const PREVIEW_SESSION_MS = 12 * 60 * 60 * 1000;

// Preserve an existing preview sign-in across the app's ordinary restart. The
// access code and exact origin remain its authority; rotating either revokes it.
// No sliding expiry, persistent credential store, or product login is introduced.
export function previewSessions(accessCode, origin) {
 const key = createHmac('sha256', accessCode).update('unified-preview-session-key-v1\0' + origin).digest();
 const sign = value => createHmac('sha256', key).update(value).digest('base64url');
 return {
  issue(now = Date.now()) {
   const body = `v1.${now + PREVIEW_SESSION_MS}.${randomBytes(32).toString('base64url')}`;
   return body + '.' + sign(body);
  },
  accepts(token, now = Date.now()) {
   if (typeof token !== 'string' || !/^v1\.[0-9]{13}\.[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}$/.test(token)) return false;
   const parts = token.split('.'), until = Number(parts[1]);
   if (!Number.isSafeInteger(until) || until <= now || until > now + PREVIEW_SESSION_MS) return false;
   const body = parts.slice(0, 3).join('.');
   return timingSafeEqual(Buffer.from(parts[3]), Buffer.from(sign(body)));
  },
 };
}
