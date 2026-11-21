#!/bin/bash
# Runs the synthetic message-interactions fixture on 127.0.0.1:8770 (it binds loopback only) and the proxy on
# port 8780 that Docker publishes. The twin exits when either stops, so the universe does not look healthy half up.
set -euo pipefail
cd /home/user/amplifier-unified
.venv/bin/python tests/fixtures/message_interactions_ui_server.py 8770 &
nginx -e stderr -c /etc/web-fixture/nginx.conf &
wait -n
