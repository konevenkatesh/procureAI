# ProcureAI m4-communicator — stateless FastAPI on Cloud Run.
# Build context: repo root (uses Cloud Build with services/m4-communicator/cloudbuild.yaml).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Install deps first (cache-friendly layer).
COPY services/m4-communicator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Shared FastAPI + Cloud Tasks + Supabase scaffolding.
COPY services/_shared /app/_shared

# Service-specific entrypoint.
COPY services/m4-communicator/app /app

EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
