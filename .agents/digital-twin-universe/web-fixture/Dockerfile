# Node 22 (what the project's CI uses) for the frontend build, copied from the official image.
FROM node:22-bookworm-slim AS node

# Python 3.13 (requires-python >= 3.13); uv is told to use this interpreter, never to download one.
FROM python:3.13-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates nginx \
 && rm -rf /var/lib/apt/lists/*

# Trust the universe's gateway, which answers github.com/microsoft/amplifier-unified with the local working tree.
COPY --from=dtu-ca ca.crt /usr/local/share/ca-certificates/digital-twin-universe.crt
RUN update-ca-certificates

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY nginx.conf /etc/web-fixture/nginx.conf
COPY start.sh /usr/local/bin/start-web-fixture
RUN chmod 0755 /usr/local/bin/start-web-fixture

RUN useradd -m user
USER user
WORKDIR /home/user
ENV PATH="/home/user/.local/bin:${PATH}" \
    UV_NATIVE_TLS=true \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.13

# The project as it would be cloned once published; the gateway serves the local working tree.
RUN git clone https://github.com/microsoft/amplifier-unified /home/user/amplifier-unified
WORKDIR /home/user/amplifier-unified
RUN uv sync --locked
RUN npm ci --prefix frontend
# Rewrites amplifier_web/static, in the twin's clone only.
RUN npm --prefix frontend run build
