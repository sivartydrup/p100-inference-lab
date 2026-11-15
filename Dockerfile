FROM node:22-bookworm-slim AS dashboard
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LAB_DB=/data/lab.sqlite3 LAB_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/ ./
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home lab && mkdir /data && chown lab:lab /data
COPY --from=dashboard /src/dist /app/static
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "lab.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
