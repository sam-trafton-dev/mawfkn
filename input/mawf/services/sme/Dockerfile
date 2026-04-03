FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ ./shared/

COPY services/sme/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/sme/ ./services/sme/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# SME_DOMAIN must be set via docker-compose environment: (e.g. "data", "api", "ux", ...)

EXPOSE 8080

CMD ["uvicorn", "services.sme.main:app", "--host", "0.0.0.0", "--port", "8080"]
