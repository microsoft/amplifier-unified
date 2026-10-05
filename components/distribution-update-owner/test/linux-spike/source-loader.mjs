// Execute the source spike without generating dist/ or packaging a release.
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const dist = new URL('../../dist/', import.meta.url).href;
const src = new URL('../../src/', import.meta.url).href;
registerHooks({ resolve(specifier, context, next) {
  if (specifier.startsWith('.') && specifier.endsWith('.js') && context.parentURL) {
    const requested = new URL(specifier, context.parentURL).href;
    // Existing focused tests import this component's dist/index.js. Redirect
    // only that component to source; no generated build or dependency rewrites.
    if (requested.startsWith(dist)) {
      const source = src + requested.slice(dist.length, -3) + '.ts';
      if (existsSync(fileURLToPath(source))) return next(source, context);
    }
    const ts = new URL(specifier.slice(0, -3) + '.ts', context.parentURL);
    if (existsSync(fileURLToPath(ts))) return next(ts.href, context);
  }
  return next(specifier, context);
} });
