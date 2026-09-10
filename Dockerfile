# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Stage 2: Production ----
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Security: run as non-root user
RUN useradd -m appuser

# Copy application code
COPY . .

# Create data directory with correct ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

# The port is whatever the host injects: Railway, Render and Cloud Run all set
# $PORT and route to it, so a fixed port makes the service unreachable there.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8000'))" || exit 1

# Shell form so $PORT is expanded at run time. One worker per container keeps
# lifecycle ownership simple; horizontal replicas share WebSocket fan-out via
# REDIS_URL in production.
#
# Migrations run here rather than in the application's lifespan: the container
# must fail loudly and stay down when the schema cannot be brought up to date,
# instead of serving requests against a database it disagrees with.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
