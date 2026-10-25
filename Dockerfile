# GreenTrace: the API (Python 3.11) and, beside it, the Meterless server (Node 24,
# meterless/server.ts: World Model and H-MEM references, Scout Intent). Sample mode by default: no key, no network.
#
#   docker build -t greentrace .
#   docker run --rm -p 8000:8000 greentrace          # then http://localhost:8000
#
# Three stages: Node dependencies, Python dependencies, then a final image that only copies
# what runs (no npm, no pip cache, no test tools, no raw map tiles).

# ---- 1. the World Model server's dependencies, from the lock file (tsx only, no vitest) ----------
FROM node:24-slim AS world-model
WORKDIR /meterless
COPY meterless/package.json meterless/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY meterless/server.ts meterless/LICENSE meterless/UPSTREAM.sha256 ./
COPY meterless/reference/src ./reference/src
COPY meterless/reference/package.json meterless/reference/tsconfig.json ./reference/
COPY meterless/hmem/reference/src ./hmem/reference/src
COPY meterless/hmem/reference/package.json meterless/hmem/reference/tsconfig.json ./hmem/reference/
COPY meterless/hmem/LICENSE meterless/hmem/UPSTREAM.sha256 ./hmem/
COPY meterless/scout/index.ts meterless/scout/greentrace_intents.json ./scout/

# ---- 2. the Python dependencies in a virtual environment, and the reference cells indexed --------
FROM python:3.11-slim AS python-build
WORKDIR /app
COPY requirements.txt .
RUN grep -v '^pytest' requirements.txt > runtime.txt \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r runtime.txt \
    && /opt/venv/bin/pip uninstall --yes --quiet pip setuptools  # not needed to run, 28 MB
COPY app ./app
COPY data ./data
RUN /opt/venv/bin/python -c "from app.world.store import reference_db; print(reference_db())"

# ---- 3. what runs ------------------------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    SAMPLE_MODE=true \
    PORT=8000 \
    WORLD_MODEL_URL=http://127.0.0.1:8787 \
    HMEM_URL=http://127.0.0.1:8787/hmem

RUN useradd --create-home greentrace
COPY --from=world-model /usr/local/bin/node /usr/local/bin/node
COPY --from=python-build /opt/venv /opt/venv

WORKDIR /app
COPY --chown=greentrace --from=world-model /meterless ./meterless
COPY --chown=greentrace --from=python-build /app/data ./data
COPY --chown=greentrace app ./app
COPY --chown=greentrace examples ./examples
COPY --chown=greentrace scripts ./scripts
COPY --chown=greentrace run.py verify.py docker-start.sh ./
RUN mkdir logs && chown greentrace logs
USER greentrace

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["sh", "docker-start.sh"]
