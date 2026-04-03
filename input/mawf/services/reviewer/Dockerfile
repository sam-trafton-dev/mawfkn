FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ ./shared/

COPY services/reviewer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/reviewer/ ./services/reviewer/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8002

CMD ["uvicorn", "services.reviewer.main:app", "--host", "0.0.0.0", "--port", "8002"]
