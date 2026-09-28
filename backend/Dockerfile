# ---- 构建依赖 ----
FROM node:22-bookworm-slim AS protocol
WORKDIR /protocol-runtime
COPY protocol-runtime/package*.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- 运行镜像 ----
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app && adduser --system --ingroup app app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY --from=protocol /usr/local/bin/node /usr/local/bin/node
COPY --from=protocol /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY . .
COPY --from=protocol /protocol-runtime/node_modules /app/protocol-runtime/node_modules
RUN mkdir -p /app/data/documents /app/data/datasets /app/data/logs && chown -R app:app /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
