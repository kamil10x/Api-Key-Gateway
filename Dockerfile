FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Create a non-root user for container security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy packaging metadata and source (add alembic.ini & alembic/ here on Day 5)
COPY pyproject.toml ./
COPY src ./src

# Install the package and its runtime dependencies
RUN pip install --no-cache-dir .

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" || exit 1

CMD ["uvicorn", "quotes_service.main:app", "--host", "0.0.0.0", "--port", "8000"]