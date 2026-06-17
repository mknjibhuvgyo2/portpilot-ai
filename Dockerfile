# AI Port Hub — multi-stage production image.
# Stage 1 builds the Vue frontend; stage 2 runs the FastAPI backend that serves
# the built static bundle, so the whole app ships as one container / one process.

# ---------- Stage 1: build frontend ----------
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
# install deps first for better layer caching
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build   # -> /app/frontend/dist

# ---------- Stage 2: backend runtime ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HUB_HOST=0.0.0.0 \
    HUB_PORT=8000

WORKDIR /app

# ffmpeg: video frame extraction for vision/eval templates
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# backend deps
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# backend source + built frontend (the backend serves frontend/dist)
COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist

# runtime data (sqlite db / secret key / prompts) lives here; mount a volume to persist
RUN mkdir -p /app/data
VOLUME ["/app/data"]

WORKDIR /app/backend
EXPOSE 8000

# Note: gateway-proxied port services bind their own ports inside the container;
# publish/forward those extra ports as needed (-p) or run with --network host.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
