# GreenTrace: the API (Python 3.11) and, beside it, the Meterless World Model reference
# server (Node 24, meterless/server.ts). Sample mode by default: no key, no network.
#
#   docker build -t greentrace .
#   docker run --rm -p 8000:8000 greentrace          # then http://localhost:8000/health

# ---- Node 24: the World Model server's dependencies, installed from the lock file ----------
FROM node:24-slim AS world-model
WORKDIR /meterless
COPY meterless/package.json meterless/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY meterless/server.ts meterless/LICENSE meterless/UPSTREAM.sha256 ./
COPY meterless/reference/src ./reference/src
COPY meterless/reference/package.json meterless/reference/tsconfig.json ./reference/

# ---- the app ---------------------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SAMPLE_MODE=true \
    PORT=8000 \
    WORLD_MODEL_URL=http://127.0.0.1:8787

COPY --from=world-model /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=world-model /meterless ./meterless
COPY app ./app
COPY data ./data
COPY examples ./examples
COPY scripts ./scripts
COPY run.py verify.py docker-start.sh ./
# The reference cells, indexed once here so the first run does not wait for them.
RUN python -c "from app.world.store import reference_db; print(reference_db())" \
    && mkdir -p logs && useradd --create-home greentrace && chown -R greentrace /app
USER greentrace

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["sh", "docker-start.sh"]
