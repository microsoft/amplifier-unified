# A test-only image. The managed Compose runner supplies no host dependencies,
# mounts or credentials to it. Networked dependency resolution occurs at build.
FROM node:22-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.13.9 AS uv
FROM python:3.13-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=uv /uv /usr/local/bin/uv
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential openssl iproute2 dbus avahi-daemon avahi-utils python3-gi \
    && apt-get clean
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/paired/browsers \
    GIT_TERMINAL_PROMPT=0 PYTHONDONTWRITEBYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /opt/paired/source
COPY tests/web/package.json tests/web/package-lock.json tests/web/
RUN npm ci --prefix tests/web \
    && tests/web/node_modules/.bin/playwright install --with-deps chromium \
    && chmod -R a+rX /opt/paired/browsers
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
# Explicit copied-source inputs. Never silently build an old public selection.
# With an accepted public client, set delivery.type=repository in selection.json
# and omit the archive. For a private client use the Git archive and directory
# delivery below. These fixture inputs are staged by the integration owner.
RUN set -eu; candidate=tests/web/linked-client-candidate; \
    case "$(cat "$candidate/fixture-mode")" in ready|blocked) ;; *) exit 1;; esac; \
    delivery="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["delivery"]["type"])' "$candidate/selection.json")"; \
    if test "$delivery" = directory; then \
      (cd "$candidate" && if test -f static-component.tar.gz; then \
        sha256sum fixture-mode selection.json client-source.tar.gz static-component.tar.gz; \
      else sha256sum fixture-mode selection.json client-source.tar.gz; fi | cmp - SHA256SUMS); \
      test "$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["delivery"]["path"])' "$candidate/selection.json")" = .build/paired-candidate-source; \
      mkdir -p /opt/paired/source/.build/paired-candidate-source; \
      expected="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"]["revision"])' "$candidate/selection.json")"; \
      actual="$(gzip -dc "$candidate/client-source.tar.gz" | git get-tar-commit-id)"; \
      test "$expected" = "$actual"; \
      tar -xzf "$candidate/client-source.tar.gz" -C .build/paired-candidate-source; \
    else \
      test "$delivery" = repository && test ! -e "$candidate/client-source.tar.gz"; \
      (cd "$candidate" && sha256sum fixture-mode selection.json | cmp - SHA256SUMS); \
    fi; \
    cp "$candidate/selection.json" components/web-client.source.json
# The public repository-delivery snapshot includes just a manifest staging
# directory. Keep it as input evidence while assembling in a clean directory.
RUN if test -d .build/web-client; then mv .build/web-client /opt/paired/copied-client-manifests; fi \
    && python scripts/web_client_build.py prepare
# Do not replace a directory copied into an earlier image layer: its rename
# can fail with EXDEV. Fail clearly if a builder ignored the context exclusion.
RUN if test -e amplifier_web/static || test -L amplifier_web/static; then \
      echo 'Paired fixture requires no pre-existing amplifier_web/static in the image' >&2; exit 1; \
    fi \
    && if test -f tests/web/linked-client-candidate/static-component.tar.gz; then \
      mkdir /opt/paired/client-package; \
      tar -xzf tests/web/linked-client-candidate/static-component.tar.gz -C /opt/paired/client-package; \
      .venv/bin/python tests/web/linked-fresh-install.py install_static; \
    else \
      npm ci --prefix .build/web-client && python scripts/web_client_build.py build \
        && python scripts/web_client_build.py verify >/opt/paired/verified-client.json; \
    fi
# The checkout alone does not provide top-level imports for selected components
# under components/*-delivery. Install the exact project with its normal build
# hook once all copied source and rebuilt assets are present.
RUN uv sync --locked --no-dev \
    && .venv/bin/python -c 'import amplifier_web.server'
ARG FIXTURE_AVAHI_ID=
# Avahi validates its runtime directory against this account even with --no-drop-root.
# Align the account at build time; the app guest starts nonroot with no capabilities.
RUN if test -n "$FIXTURE_AVAHI_ID"; then \
      case "$FIXTURE_AVAHI_ID" in 24031|24032) ;; *) exit 1;; esac; \
      groupmod -g "$FIXTURE_AVAHI_ID" avahi; \
      usermod -u "$FIXTURE_AVAHI_ID" -g avahi avahi; \
    fi \
    && install -d -m 0700 -o 24031 -g 24031 /home/fixture-source \
    && install -d -m 0700 -o 24032 -g 24032 /home/fixture-destination \
    && install -d -m 0755 -o 24031 -g 24031 /run/link-source \
    && install -d -m 0755 -o 24032 -g 24032 /run/link-destination \
    && install -d -m 1777 /run/link-control \
    && install -m 0644 -o 24031 -g 24031 /dev/null /run/link-source/.volume-seed \
    && install -m 0644 -o 24032 -g 24032 /dev/null /run/link-destination/.volume-seed \
    && install -m 0644 /dev/null /run/link-control/.volume-seed
ENV AMPLIFIER_TEST_PYTHON=/opt/paired/source/.venv/bin/python