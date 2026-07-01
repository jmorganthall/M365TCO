# Multi-stage build: compile the React front end, then ship it inside the
# FastAPI image so the whole tool runs as a single container. Targets Unraid;
# the same image runs on Azure Container Apps (set TCO_DATABASE_URL to Postgres
# and TCO_DATA_DIR to a mounted volume / use Azure Key Vault for secrets).

# ---- Stage 1: build the front end ----
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: backend runtime ----
FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TCO_DATABASE_URL=sqlite:////data/tco.db \
    TCO_DATA_DIR=/data

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./
# Built SPA served by FastAPI from /app/static (see app/main.py).
COPY --from=frontend /fe/dist ./static

# Persistent data (SQLite DB + encrypted secret store) lives on a volume.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
# --proxy-headers + trusting forwarded IPs so the app sees the external origin
# (X-Forwarded-Proto/Host) behind a reverse proxy — needed for the auto-derived
# OAuth redirect URI to match what the browser used.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
