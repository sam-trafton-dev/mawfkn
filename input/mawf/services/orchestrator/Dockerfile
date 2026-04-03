FROM python:3.12-slim

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy shared package first (build context is repo root)
COPY shared/ ./shared/

# Copy service-specific files
COPY services/orchestrator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/orchestrator/ ./services/orchestrator/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "services.orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
