#!/bin/sh
# The container's two processes: the Meterless World Model server on 127.0.0.1:8787 (this
# container only, it has no authentication), then the API in the foreground on $PORT.
# If the World Model server is not up, the agents use the in-process fallback and say so.
set -e
cd /app/meterless
node node_modules/tsx/dist/cli.mjs server.ts &
cd /app
for _ in 1 2 3 4 5 6 7 8 9 10; do
  python -c "import urllib.request; urllib.request.urlopen('$WORLD_MODEL_URL/health', timeout=1)" 2>/dev/null && break
  sleep 0.5
done
exec python run.py
