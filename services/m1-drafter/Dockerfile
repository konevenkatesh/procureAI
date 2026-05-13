# ProcureAI m1-drafter v2 — multi-module FastAPI on Cloud Run.
#
# Layout difference from R3 stub: M1.5 added 5 sub-modules with relative
# imports (`from .schemas import ...`). We preserve the `app` package
# structure inside the image so the imports resolve correctly. uvicorn
# is launched as `app.main:app` (not bare `main:app`).
#
# Build context: repo root. Driven by services/m1-drafter/cloudbuild.yaml.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Install deps first (cache-friendly layer).
COPY services/m1-drafter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Shared FastAPI + Cloud Tasks + Supabase scaffolding at /app/_shared (top-level)
COPY services/_shared /app/_shared

# m1-drafter app package at /app/app (so `from .schemas import` resolves)
COPY services/m1-drafter/app /app/app

# R10.0 fix: builder/ holds builder.config.settings (Supabase URL + keys)
# used by workflow_v2's pgvector path. Without this COPY the R9.4 production
# logs showed `pgvector DB unavailable: No module named 'builder'`, forcing
# retrieval to the in-memory registry fallback.
COPY builder /app/builder

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
