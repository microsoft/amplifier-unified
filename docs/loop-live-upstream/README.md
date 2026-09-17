Prepared upstream change against bkrabach/amplifier-module-loop-live bb9f5966d285ee4a93f4d84309aacf4bd9a09b5a.

This patch adds portable, identified manager-turn completion, distinct delivered and accepted inputs, and active-job metadata. Core tests use fixtures (28 passed); no voice provider claim follows from these tests. The application packages the modified core source in runtime_deps/vendor/loop-live until an upstream reviewed commit can replace it.

Apply with git apply identified-generation-completion.patch in that base checkout. No push or pull request has been created.
