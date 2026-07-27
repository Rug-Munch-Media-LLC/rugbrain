# rmi-rag — multi-mode container
# Default: FastAPI server (uvicorn)
# Override CMD for ingest: docker run ... python scripts/ingest_threats.py

FROM python:3.11-slim

# ── System deps ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps (ordered for layer caching) ──────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY scripts/ scripts/
COPY pyproject.toml .

RUN pip install --no-cache-dir -e .

# ── Security: non-root user ──────────────────────────────────────────
RUN groupadd -r rmi && useradd -r -g rmi -s /bin/false rmi && chown -R rmi:rmi /app
USER rmi

# ── Expose API port ──────────────────────────────────────────────────
EXPOSE 8012

# ── Health check ─────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8012/ready'); exit(0 if r.status_code == 200 else 1)" \
    || exit 1

# ── Default: start FastAPI server ───────────────────────────────────
CMD ["uvicorn", "rmi_rag.main:app", "--host", "0.0.0.0", "--port", "8012", "--log-level", "info"]
