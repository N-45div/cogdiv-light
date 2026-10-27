# syntax=docker/dockerfile:1

# ---- Stage 1: build the React app into static files -----------------------------------
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
# Copy the lockfile first so the dependency layer is cached until dependencies change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime serving the API and the built site -----------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/wind_monitor ./wind_monitor
COPY backend/data ./data
COPY --from=frontend /app/frontend/dist /app/static

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app
USER app

ENV STATIC_DIR=/app/static \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["sh", "-c", "exec uvicorn wind_monitor.main:app --host 0.0.0.0 --port ${PORT}"]
