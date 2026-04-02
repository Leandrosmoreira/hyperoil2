# HyperOil v2 — Multi-stage Dockerfile

# --- Build stage ---
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# --- Runtime stage ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code and config
COPY src/ src/
COPY config.yaml .

# Create data directory
RUN mkdir -p /app/data/jsonl

# Non-root user for security
RUN useradd --create-home --no-log-init appuser \
    && chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Expose health port
EXPOSE 8080

# Entry point
ENTRYPOINT ["python", "-m", "hyperoil"]
CMD ["--log-format", "json"]
